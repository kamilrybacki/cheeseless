"""A toy repository with a hidden suite, built with plain git for tests (Q29)."""

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

CANARY = "CHEESELESS-CANARY-7f3a9c"
SENTINEL = "CHEESELESS-SENTINEL-3c9d1e"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Toy Author",
    "GIT_AUTHOR_EMAIL": "toy@example.invalid",
    "GIT_COMMITTER_NAME": "Toy Author",
    "GIT_COMMITTER_EMAIL": "toy@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}

CRITERIA_YAML = """\
task: toy
run: [python3, -m, toy]
env:
  PYTHONPATH: src
criteria:
  - id: normalize
    description: The normalize command lowercases lines and collapses whitespace.
  - id: score
    description: The score command orders words by length, longest first.
"""

SMOKE_TEST = """\
def test_starts(candidate):
    result = candidate.run(["--version"])
    assert result.returncode == 0
"""

BASELINE_MAIN = """\
import sys


def main(argv):
    if argv[:1] == ["--version"]:
        print("toy 0.0.1")
        return 0
    sys.stdout.write(sys.stdin.read())
    return 0


raise SystemExit(main(sys.argv[1:]))
"""

FIXED_MAIN = """\
import sys


def normalize(text):
    return "".join(" ".join(line.split()).lower() + "\\n" for line in text.splitlines())


def score(text):
    words = [w for w in text.split() if w]
    return "".join(w + "\\n" for w in sorted(words, key=lambda w: (-len(w), w)))


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

HIDDEN_NORMALIZE = f"""\
# {CANARY}
import pytest

pytestmark = pytest.mark.criterion("normalize")


def test_lowercases(candidate):
    assert candidate.run(["normalize"], stdin="Hello World\\n").stdout == "hello world\\n"


def test_collapses_whitespace(candidate):
    assert candidate.run(["normalize"], stdin="  a \\t  b  \\n").stdout == "a b\\n"


def test_keeps_lines(candidate):
    assert candidate.run(["normalize"], stdin="X\\nY\\n").stdout == "x\\ny\\n"
"""

HIDDEN_SCORE = f"""\
# {CANARY}
# {SENTINEL}
import pytest

pytestmark = pytest.mark.criterion("score")


def test_longest_first(candidate):
    assert candidate.run(["score"], stdin="aa a aaa\\n").stdout == "aaa\\naa\\na\\n"


def test_ties_alphabetical(candidate):
    assert candidate.run(["score"], stdin="bb aa\\n").stdout == "aa\\nbb\\n"


def test_empty_input(candidate):
    assert candidate.run(["score"], stdin="").stdout == ""
"""

TOY_FILES: Mapping[str, str] = {
    "README.md": "# toy\n",
    "src/toy/__init__.py": "",
    "src/toy/__main__.py": BASELINE_MAIN,
    "acceptance/contract/criteria.yaml": CRITERIA_YAML,
    "acceptance/contract/test_smoke.py": SMOKE_TEST,
    "acceptance/hidden/test_normalize.py": HIDDEN_NORMALIZE,
    "acceptance/hidden/test_score.py": HIDDEN_SCORE,
}


def git(root: Path, *args: str, input: bytes | None = None) -> str:
    env = {**os.environ, **GIT_ENV}
    out = subprocess.run(  # noqa: S603 - fixed git argv in tests
        ["git", *args],  # noqa: S607
        cwd=root,
        env=env,
        input=input,
        capture_output=True,
        check=True,
    )
    return out.stdout.decode().strip()


def write_files(root: Path, files: Mapping[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def commit_all(root: Path, message: str = "change") -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", message)
    return git(root, "rev-parse", "HEAD")


def make_repo(root: Path, files: Mapping[str, str] = TOY_FILES) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "--initial-branch=main")
    write_files(root, files)
    commit_all(root, "toy baseline")
    return root


def freeze_without_approval(state: str) -> int:
    """Freeze the toy for a test that is not about the approval gate.

    `cheeseless freeze --yes` no longer exists: every route to a frozen suite goes through
    approval, because that is the gate ADR 0016 exists to protect. Tests that need a frozen
    task as a *given* - the CI bundle, the budget ledger, the sentinel redaction - call this
    instead, so the shortcut they depend on is named, local to the tests, and impossible to
    reach from a production command line: it calls the app layer directly, and the CLI has
    no flag, hidden or not, that commits without the gate.
    """
    from cheeseless.app.freeze import FreezeError, commit, prepare  # noqa: PLC0415
    from cheeseless.bootstrap import build_context  # noqa: PLC0415 - keeps collection light

    ctx = build_context(Path(state))
    try:
        prepared = prepare(ctx.repo, ctx.runner, ctx.sandbox)
    except FreezeError as exc:
        print(f"freeze failed: {'; '.join(exc.problems)}", file=sys.stderr)
        return 1
    result = commit(prepared, ctx.store)
    print(f"frozen task: {result.task_id}")
    return 0
