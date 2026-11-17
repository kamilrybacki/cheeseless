"""Scripted stand-in agents for the author-side demo (they run inside cheeseless sessions).

These are deterministic scripts, not language models. Each step plays one role in a
cheeseless session and talks to the verifier only through the session socket, the same
way an MCP-connected agent would. The host copies this file into the sandbox HOME and
runs it with ``/usr/bin/python3 -I``; the working directory is the session projection.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REFERENCE_MAIN = """\
import sys


def normalize(text):
    return "".join(" ".join(line.split()).lower() + "\\n" for line in text.splitlines())


def score(text):
    # The spec: longest first, equal lengths keep their input order (a stable sort).
    return "".join(w + "\\n" for w in sorted(text.split(), key=len, reverse=True))


def main(argv):
    if argv[:1] == ["--version"]:
        print("toy 0.1.0")
        return 0
    command = {"normalize": normalize, "score": score}.get(argv[0] if argv else "")
    if command is None:
        print("unknown command", file=sys.stderr)
        return 2
    sys.stdout.write(command(sys.stdin.read()))
    return 0


raise SystemExit(main(sys.argv[1:]))
"""

# An implementer that guessed alphabetical tie-breaking instead of reading the spec.
GUESSED_MAIN = REFERENCE_MAIN.replace(
    "sorted(text.split(), key=len, reverse=True)",
    "sorted(text.split(), key=lambda w: (-len(w), w))",
).replace(
    "# The spec: longest first, equal lengths keep their input order (a stable sort).",
    "# Ties alphabetical, which looks tidy.",
)

NORMALIZE_TESTS = """\
# CHEESELESS-SENTINEL-demo-normalize
import pytest

pytestmark = pytest.mark.criterion("normalize")


def test_lowercases(candidate):
    assert candidate.run(["normalize"], stdin="Hello World\\n").stdout == "hello world\\n"


def test_collapses_whitespace(candidate):
    assert candidate.run(["normalize"], stdin="  a \\t  b  \\n").stdout == "a b\\n"
"""

SCORE_HEAD = """\
# CHEESELESS-SENTINEL-demo-score
import pytest

pytestmark = pytest.mark.criterion("score")


def test_longest_first(candidate):
    out = candidate.run(["score"], stdin="a ccc bb aa\\n").stdout
    assert out == "ccc\\nbb\\naa\\na\\n"
"""

# The deliberate bait: this test contradicts the spec, which says ties keep input order.
BAIT_TIE_TEST = """

def test_ties_alphabetical(candidate):
    assert candidate.run(["score"], stdin="bb aa\\n").stdout == "aa\\nbb\\n"
"""

FIXED_TIE_TEST = """

def test_ties_keep_input_order(candidate):
    assert candidate.run(["score"], stdin="bb aa\\n").stdout == "bb\\naa\\n"
"""


def _client(role: str):  # noqa: ANN202 - the client type lives in the sandbox runtime
    sys.path.insert(0, os.environ["CHEESELESS_RUNTIME"])
    from cheeseless.adapters.inbound.uds.client import UdsVerifierClient

    return UdsVerifierClient(
        os.environ["CHEESELESS_SOCKET"], os.environ.get("CHEESELESS_TASK"), role=role, timeout=180
    )


def _say(text: str) -> None:
    print(f"  agent> {text}", flush=True)


def _visible_files() -> list[str]:
    root = Path.cwd()
    return sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    )


def _write(rel: str, text: str) -> None:
    path = Path.cwd() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _say(f"wrote {rel}")


def _describe(outcome: object) -> str:
    if isinstance(outcome, dict):
        return ", ".join(f"{key}={value}" for key, value in sorted(outcome.items()))
    code = getattr(outcome, "code", None)
    return f"refused: {getattr(code, 'value', outcome)}"


def reference() -> None:
    files = _visible_files()
    _say(f"I can see {len(files)} files: " + ", ".join(files))
    tests = [f for f in files if Path(f).name.startswith("test_")]
    _say(f"test files visible to me: {len(tests)}")
    spec = (Path.cwd() / "acceptance/contract/spec.md").read_text(encoding="utf-8")
    rule = next(line for line in spec.splitlines() if "equal length" in line)
    _say(f"implementing from the spec: {rule.strip('- ').strip()}")
    _write("src/toy/__main__.py", REFERENCE_MAIN)


def _author_round(tie_test: str) -> None:
    client = _client("author")
    _write("acceptance/hidden/test_normalize.py", NORMALIZE_TESTS)
    _write("acceptance/hidden/test_score.py", SCORE_HEAD + tie_test)
    _say("author_check       -> " + _describe(client.author_check()))
    _say("author_crosscheck  -> " + _describe(client.author_crosscheck()))
    _say("freeze_propose     -> " + _describe(client.freeze_propose()))


def author_bait() -> None:
    _say("writing hidden tests, including test_ties_alphabetical (contradicts the spec)")
    _author_round(BAIT_TIE_TEST)


def author_fix() -> None:
    _say("the crosscheck was red on score; fixing only the test that contradicts the spec")
    _author_round(FIXED_TIE_TEST)


def author_late() -> None:
    client = _client("author")
    _say("trying to change the frozen suite")
    _say("author_check       -> " + _describe(client.author_check()))
    _say("freeze_propose     -> " + _describe(client.freeze_propose()))


def implementer() -> None:
    sys.path.insert(0, os.environ["CHEESELESS_RUNTIME"])
    from cheeseless.domain.dispute import DisputeCode

    client = _client("implementer")
    tests = [f for f in _visible_files() if f.startswith("acceptance/hidden")]
    _say(f"hidden test files visible to me: {len(tests)}")
    _write("src/toy/__main__.py", GUESSED_MAIN)
    outcome = client.run().outcome
    status = getattr(getattr(outcome, "status", None), "value", None)
    if status is None:
        _say("run -> " + _describe(outcome))
    else:
        per = ", ".join(f"{c.criterion_id}={c.status.value}" for c in outcome.criteria)
        _say(f"run -> {status} ({per})")
    note = "score fails but my output is sorted; I think the tie rule is wrong"
    ack = client.dispute("score", DisputeCode.CONTRADICTS_SPEC, note)
    _say("dispute(score, contradicts_spec) -> " + (ack if isinstance(ack, str) else _describe(ack)))


STEPS = {
    "reference": reference,
    "author-bait": author_bait,
    "author-fix": author_fix,
    "author-late": author_late,
    "implementer": implementer,
}

if __name__ == "__main__":
    STEPS[sys.argv[1]]()
