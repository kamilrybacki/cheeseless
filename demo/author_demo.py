"""Record the author side of cheeseless: reference, authoring, freeze, late edits, disputes.

The run drives the real ``cheeseless`` CLI against a scratch repository. The agents are
scripted stand-ins (``demo/author_agents.py``), not language models, so the recording is
deterministic. Output is scrubbed of absolute paths, and every hex id is replaced with a
stable label before anything is written.

    uv run --frozen python -m demo.author_demo --out docs/demo
"""

from __future__ import annotations

import argparse
import fcntl
import html
import json
import os
import pty
import re
import select
import subprocess  # noqa: S404 - the demo drives the real cheeseless CLI and git
import sys
import tempfile
import termios
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from demo.run_demo import Recorder, scrub

COMMAND = "uv run --frozen python -m demo.author_demo --out docs/demo"
AGENTS = Path(__file__).with_name("author_agents.py")
CHEESELESS = Path(sys.executable).parent / "cheeseless"
PLACEHOLDER_TASK = "0" * 64

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Demo Host",
    "GIT_AUTHOR_EMAIL": "host@example.invalid",
    "GIT_COMMITTER_NAME": "Demo Host",
    "GIT_COMMITTER_EMAIL": "host@example.invalid",
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
    description: >-
      The score command prints words longest first; words of equal length keep
      their input order.
"""

SPEC_MD = """\
# toy

`python3 -m toy <command>` reads stdin and writes stdout.

## normalize

- every line is lowercased
- runs of whitespace collapse to one space

## score

- one word per line, longest first
- words of equal length keep their input order (a stable sort)
"""

SMOKE_TEST = """\
def test_starts(candidate):
    assert candidate.run(["--version"]).returncode == 0
"""

BASELINE_MAIN = """\
import sys

if sys.argv[1:2] == ["--version"]:
    print("toy 0.0.1")
else:
    sys.stdout.write(sys.stdin.read())
"""

FILES = {
    "README.md": "# toy\n",
    "src/toy/__init__.py": "",
    "src/toy/__main__.py": BASELINE_MAIN,
    "acceptance/contract/criteria.yaml": CRITERIA_YAML,
    "acceptance/contract/spec.md": SPEC_MD,
    "acceptance/contract/test_smoke.py": SMOKE_TEST,
}

_HEX = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{64}|[0-9a-f]{40})(?![0-9a-f])")


_CHALLENGE = re.compile(r"type this code to confirm: ([A-Z2-9]{8})\r?\n")


def find_challenge_code(text: str) -> str | None:
    """The code from a finished ``type this code to confirm:`` line, or None."""
    found = _CHALLENGE.search(text)
    return found.group(1) if found else None


@dataclass(frozen=True)
class PtyResult:
    returncode: int
    output: str
    typed: str | None


def run_on_pty(
    argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
) -> PtyResult:
    """Run ``argv`` with a controlling terminal this process owns and answer the challenge.

    The child's own stdio and its ``/dev/tty`` are the same pty. The code is read off the
    terminal and typed back, the way an operator would (ADR 0017). Nothing is piped."""
    master, slave = pty.openpty()

    def adopt_terminal() -> None:  # runs in the child, after setsid
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(  # noqa: S603 - the venv's own cheeseless entry point
        list(argv),
        cwd=cwd,
        env=dict(env),
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
        preexec_fn=adopt_terminal,  # noqa: PLW1509 - needed to take the controlling terminal
    )
    os.close(slave)
    seen = b""
    typed: str | None = None
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:  # EIO: every slave end is closed
                    break
                if not chunk:
                    break
                seen += chunk
                code = find_challenge_code(seen.decode("utf-8", errors="replace"))
                if code and typed is None:
                    typed = code
                    os.write(master, code.encode("ascii") + b"\n")
            elif proc.poll() is not None:
                break
        else:
            proc.kill()
            raise TimeoutError(f"pty command exceeded {timeout}s")
        returncode = proc.wait(timeout=30)
    finally:
        os.close(master)
        if proc.poll() is None:
            proc.kill()
    return PtyResult(returncode, seen.decode("utf-8", errors="replace"), typed)


class Masker:
    """Replace each distinct hex id with a stable, human-readable label."""

    def __init__(self) -> None:
        self.labels: dict[str, str] = {PLACEHOLDER_TASK: "<placeholder-task>"}

    def name(self, value: str, label: str) -> None:
        self.labels.setdefault(value, label)

    def __call__(self, text: str) -> str:
        for pid in re.findall(r"proposal_id=([0-9a-f]{64})", text):
            count = sum(1 for v in self.labels.values() if v.startswith("<proposal-"))
            self.name(pid, f"<proposal-{count + 1}>")

        def label(match: re.Match[str]) -> str:
            value = match.group(0)
            if value not in self.labels:
                count = sum(1 for v in self.labels.values() if v.startswith("<id-"))
                self.labels[value] = f"<id-{count + 1}>"
            return self.labels[value]

        return _HEX.sub(label, scrub(text))


class Demo:
    def __init__(self, work: Path, recorder: Recorder) -> None:
        self.work = work
        self.repo = work / "repo"
        self.state = work / "state"
        self.rec = recorder
        self.mask = Masker()
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(work / "home"), **GIT_ENV}
        (work / "home").mkdir()

    # -- output -----------------------------------------------------------------

    def caption(self, *lines: str) -> None:
        self.rec.write("\r\n" + "".join(f"# {line}\r\n" for line in lines))

    def show(self, display: str, output: str) -> None:
        text = self.mask(output).rstrip("\n")
        body = "".join(f"{line}\r\n" for line in text.splitlines()) if text else ""
        self.rec.write(f"$ {display}\r\n{body}")

    # -- processes --------------------------------------------------------------

    def git(self, *args: str) -> str:
        done = subprocess.run(  # noqa: S603 - fixed git argv
            ["git", *args],  # noqa: S607 - git from the fixed demo PATH
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout

    def cli(self, display: str, *args: str, expect: int | None = 0) -> str:
        done = subprocess.run(  # noqa: S603 - the venv's own cheeseless entry point
            [str(CHEESELESS), "--state-dir", str(self.state), *args],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        output = done.stdout + done.stderr
        self.show(f"cheeseless {display}", output)
        if expect is not None and done.returncode != expect:
            raise RuntimeError(f"{display}: exit {done.returncode}\n{self.mask(output)}")
        return output

    def cli_tty(self, display: str, *args: str, expect: int | None = 0) -> str:
        """Run a gated decision on a pty the demo owns, answering the typed challenge."""
        result = run_on_pty(
            [str(CHEESELESS), "--state-dir", str(self.state), *args],
            cwd=self.repo,
            env=self.env,
            timeout=600,
        )
        output = result.output.replace("\r\n", "\n")
        if result.typed:
            output = output.replace(
                f"type this code to confirm: {result.typed}\n",
                f"type this code to confirm: {result.typed}\n(typed back: {result.typed})\n",
            )
        self.show(f"cheeseless {display}", output)
        if expect is not None and result.returncode != expect:
            raise RuntimeError(f"{display}: exit {result.returncode}\n{self.mask(output)}")
        return output

    def session(self, role: str, step: str, projection: Path, *, placeholder: bool) -> str:
        task = ["--task", PLACEHOLDER_TASK] if placeholder else []
        shown_task = " --task <placeholder-task>" if placeholder else ""
        agent = 'exec /usr/bin/python3 -I "$HOME/.demo/author_agents.py" "$1"'
        return self.cli(
            f"session --role {role}{shown_task} --projection <{projection.name}> "
            f"-- <scripted agent: {step}>",
            "session",
            "--role",
            role,
            *task,
            "--projection",
            str(projection),
            "--home-seed",
            f".demo/author_agents.py={AGENTS}",
            "--timeout",
            "300",
            "--",
            "/bin/sh",
            "-c",
            agent,
            "sh",
            step,
        )

    # -- the story --------------------------------------------------------------

    def setup(self) -> None:
        self.repo.mkdir()
        for rel, text in FILES.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "toy: contract and baseline")
        self.mask.name(self.git("rev-parse", "HEAD").strip(), "<commit-1>")
        self.caption(
            "Author side of cheeseless. The agents below are scripted stand-ins, not LLMs.",
            "The repo has a spec and a smoke test; there are no hidden tests yet.",
        )
        self.show("git ls-files", "\n".join(sorted(FILES)) + "\n")
        self.caption("The spec's tie rule, which every hidden test must agree with:")
        rule = next(line for line in SPEC_MD.splitlines() if "equal length" in line)
        self.show("grep 'equal length' acceptance/contract/spec.md", rule + "\n")

    def show_reference(self, worktree: Path) -> None:
        """Run the recorded reference on the tie input so the viewer sees what the spec means."""
        done = subprocess.run(  # noqa: S603 - the demo's own scripted reference, fixed argv
            ["/usr/bin/python3", "-I", "src/toy/__main__.py", "score"],
            cwd=worktree,
            env=self.env,
            input="bb aa\n",
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        self.show(
            f"printf 'bb aa\\n' | python3 src/toy/__main__.py score   # in <{worktree.name}>",
            done.stdout,
        )

    def merge_spec(self) -> None:
        branch = self.git("for-each-ref", "--format=%(refname:short)", "refs/heads/spec/")
        branch = branch.strip().splitlines()[-1]
        lineage = branch.removeprefix("spec/")
        self.mask.name(lineage, "<lineage>")
        self.git("merge", "-q", "--ff-only", branch)
        self.show(f"git merge --ff-only {self.mask(branch)}", "")

    def run(self) -> None:
        self.setup()
        ref = self.work / "ref-worktree"
        self.caption(
            "1. A reference session sees only the contract (spec + criteria), never tests.",
            "   It writes a reference implementation from the spec.",
        )
        self.session("reference", "reference", ref, placeholder=True)
        self.cli(
            "reference record --worktree <ref-worktree>",
            "reference",
            "record",
            "--worktree",
            str(ref),
        )
        self.caption("   What the reference prints for a tie (bb and aa have equal length):")
        self.show_reference(ref)

        self.caption(
            "2. The author writes hidden tests. One of them is deliberate bait:",
            "   test_ties_alphabetical contradicts the spec's stable tie rule.",
            "   author_crosscheck runs the tests against the reference and goes red.",
        )
        bait = self.work / "author-worktree-1"
        out = self.session("author", "author-bait", bait, placeholder=True)
        self.cli(
            "promote --role author --worktree <author-worktree-1>",
            "promote",
            "--role",
            "author",
            "--worktree",
            str(bait),
        )
        self.merge_spec()
        pid = self.proposal(out)
        self.caption("   The host tries to approve anyway; the red crosscheck blocks it.")
        self.cli_tty(
            f"freeze --approve {self.mask(pid)} --as host",
            "freeze",
            "--approve",
            pid,
            "--as",
            "host",
            expect=None,
        )

        self.caption(
            "3. The author fixes only the test that contradicted the spec.",
            "   The crosscheck turns green and the host approves the freeze.",
        )
        fixed = self.work / "author-worktree-2"
        out = self.session("author", "author-fix", fixed, placeholder=True)
        self.cli(
            "promote --role author --worktree <author-worktree-2>",
            "promote",
            "--role",
            "author",
            "--worktree",
            str(fixed),
        )
        self.merge_spec()
        pid = self.proposal(out)
        self.caption(
            "   Operator decisions need a code retyped on the terminal (ADR 0017).",
            "   The demo plays the operator on a pty it owns: it reads the code and types it back.",
        )
        self.cli_tty(
            f"freeze --approve {self.mask(pid)} --as host",
            "freeze",
            "--approve",
            pid,
            "--as",
            "host",
        )

        self.caption("4. After the freeze the author can no longer change the suite.")
        self.session("author", "author-late", self.work / "author-worktree-3", placeholder=False)

        self.caption(
            "5. The implementer never sees the hidden tests. It guessed alphabetical ties,",
            "   fails score, and disputes the criterion. The ack says nothing about tests.",
        )
        self.cli("dispute config --enable", "dispute", "config", "--enable")
        self.session("implementer", "implementer", self.work / "impl-worktree", placeholder=False)
        listing = self.cli("disputes --show-note --json", "disputes", "--show-note", "--json")
        dispute_id = json.loads(listing)["disputes"][0]["id"]
        self.caption("   The host reads the note, checks the spec and rejects the dispute.")
        self.cli_tty(
            f"dispute resolve {self.mask(dispute_id)} --rejected --as host",
            "dispute",
            "resolve",
            dispute_id,
            "--rejected",
            "--as",
            "host",
        )
        self.caption(
            "The crosscheck checks the tests against the spec (via the reference).",
            "The implementer gate catches gaming. They are two separate controls.",
        )

    def proposal(self, session_output: str) -> str:
        found = re.findall(r"proposal_id=([0-9a-f]{64})", session_output)
        if not found:
            raise RuntimeError("the author session did not report a proposal")
        return found[-1]


def build_player(cast: str) -> str:
    events = [json.loads(line) for line in cast.splitlines()[1:] if line.strip()]
    text = "".join(event[2] for event in events).replace("\r\n", "\n")
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        "<title>cheeseless: the author side</title>\n<style>\n"
        "body{background:#111;color:#ddd;font:14px/1.45 ui-monospace,monospace;margin:2rem}\n"
        "pre{white-space:pre-wrap;max-width:110ch}\nh1{font-size:1rem;color:#fff}\n"
        "</style></head><body>\n<h1>cheeseless: the author side (scripted agents)</h1>\n"
        f"<pre>{html.escape(text)}</pre>\n</body></html>\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    recorder = Recorder(width=100, height=30)
    try:
        with tempfile.TemporaryDirectory(prefix="cheeseless-author-demo-") as tmp:
            Demo(Path(tmp), recorder).run()
    except Exception:
        sys.stderr.write("".join(e[2] for e in recorder.events).replace("\r\n", "\n"))
        raise
    cast = recorder.to_asciicast()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "author-side.cast").write_text(cast, encoding="utf-8")
    (args.out / "author-player.html").write_text(build_player(cast), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
