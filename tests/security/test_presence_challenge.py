"""ADR 0017: an attended decision needs a person at the controlling terminal.

Each of the five attended decisions is driven through the real CLI in a child process.
Without a controlling terminal (a new session, stdin piped with answers) every one is
refused with the presence reason and leaves the state as it was. The positive control
runs the same CLI on a pty that is the child's controlling terminal and types the code
shown there, so a refusal above can only come from the missing terminal, not from a
broken setup. Stdin keeps a wrong answer in both cases: it is never the answer channel.
"""

from __future__ import annotations

import ast
import fcntl
import os
import pty
import re
import select
import subprocess  # noqa: S404 - the CLI runs behind a process boundary on purpose
import sys
import termios
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from cheeseless import bootstrap
from cheeseless.adapters.inbound.cli.main import build_parser
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.presence.tty import TtyPresence
from cheeseless.adapters.outbound.store.file_store import FileStore, store_root
from cheeseless.app import authoring as uc
from cheeseless.app.authoring import AuthorError
from cheeseless.app.dispute import configure_disputes, gate_dispute, resolve_dispute
from cheeseless.app.promote import promote
from cheeseless.domain.dispute import DisputeStatus
from cheeseless.domain.presence import PresenceRefused
from tests.factories import frozen_task
from tests.presence_fakes import Refusing
from tests.unit.app import test_presence_wiring as w
from tests.unit.app.test_authoring import _HookedStore

SRC = Path(__file__).resolve().parents[2] / "src" / "cheeseless"
CODE = re.compile(rb"type this code to confirm: ([A-Z0-9]+)\r?\n")
# Stdin carries plausible answers. If the gate ever read stdin, this is what it would see.
PIPED = b"ABCDEFGH\n" * 8
TIMEOUT = 120.0


# --- the five decisions, set up in the store the CLI will open --------------------------


class _Case:
    """One attended decision: its setup, its CLI argv, and whether it took effect."""

    def __init__(self, toy_repo: Path, tmp: Path) -> None:
        self.tmp = tmp
        self.state = tmp / "state"  # where the wiring helpers keep it, and the CLI looks
        self.repo = GitRepository.discover(toy_repo)
        self.store = FileStore(store_root(self.repo.common_dir(), self.state))

    def argv(self) -> list[str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def happened(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def decide(self, presence: Refusing) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class _Approve(_Case):
    def __init__(self, toy_repo: Path, tmp: Path) -> None:
        super().__init__(toy_repo, tmp)
        self.proposal = w._ready_proposal(self.repo, self.store)
        self.before = w._state(self.repo, self.store)

    def argv(self) -> list[str]:
        return ["freeze", "--approve", self.proposal.proposal_id, "--as", "op", "--json"]

    def happened(self) -> bool:
        return (
            w._state(self.repo, self.store) != self.before
            or self.store.has_task(self.proposal.proposal_id)
            or self.store.active_task_id() is not None
            or any(e["kind"] == "freeze-approve" for e in w._events(self.store))
        )

    def decide(self, presence: Refusing) -> None:
        w._approve(self.repo, self.store, self.tmp, self.proposal, presence)


class _AllowAuto(_Case):
    def __init__(self, toy_repo: Path, tmp: Path) -> None:
        super().__init__(toy_repo, tmp)
        self.lineage = w._state(self.repo, self.store).lineage_id

    def argv(self) -> list[str]:
        return ["freeze", "--allow-auto", "--json"]

    def happened(self) -> bool:
        return self.store.auto_freeze_enabled(self.lineage)

    def decide(self, presence: Refusing) -> None:
        uc.configure_auto_freeze(self.repo, self.store, enabled=True, presence=presence)


class _Dispute(_Case):
    def __init__(self, toy_repo: Path, tmp: Path) -> None:
        super().__init__(toy_repo, tmp)
        task = frozen_task()
        self.store.save_task(task, bytes(range(32)))
        configure_disputes(self.store, enabled=True, cap=3)
        gate_dispute(
            self.store,
            task_id=task.task.task_id,
            criterion_id="normalize",
            code="untestable",
            note="why",
        )
        (self.record,) = self.store.disputes()

    def argv(self) -> list[str]:
        return ["dispute", "resolve", self.record.dispute_id, "--upheld", "--as", "op", "--json"]

    def happened(self) -> bool:
        return self.store.disputes()[0].status is not DisputeStatus.OPEN

    def decide(self, presence: Refusing) -> None:
        resolve_dispute(
            self.store, self.record.dispute_id, upheld=True, actor="op", presence=presence
        )


class _Recover(_Case):
    def __init__(self, toy_repo: Path, tmp: Path) -> None:
        super().__init__(toy_repo, tmp)
        stranding = _HookedStore(self.store.root)
        self.proposal = w._strand(self.repo, stranding, tmp, publish=True)
        self.before = w._state(self.repo, self.store)

    def argv(self) -> list[str]:
        return ["author", "recover", "--task", self.proposal.proposal_id, "--as", "op", "--json"]

    def happened(self) -> bool:
        return (
            w._state(self.repo, self.store) != self.before
            or self.store.active_task_id() is not None
        )

    def decide(self, presence: Refusing) -> None:
        w._recover(self.repo, self.store, self.tmp, self.proposal, presence)


class _Promote(_Case):
    def __init__(self, toy_repo: Path, tmp: Path) -> None:
        super().__init__(toy_repo, tmp)
        _, _, self.wt = w._evaluate(toy_repo, tmp, touch_sensitive=True)

    def argv(self) -> list[str]:
        return ["promote", "--worktree", str(self.wt), "--approve", w.SENSITIVE, "--json"]

    def happened(self) -> bool:
        return any(e["kind"] == "promote" for e in w._events(self.store))

    def decide(self, presence: Refusing) -> None:
        promote(self.repo, self.store, worktree=self.wt, approved=(w.SENSITIVE,), presence=presence)


CASES: dict[str, Callable[[Path, Path], _Case]] = {
    "freeze-approve": _Approve,
    "auto-freeze-enable": _AllowAuto,
    "dispute-resolve": _Dispute,
    "author-recover-finish": _Recover,
    "promote-sensitive": _Promote,
}


def _command(case: _Case) -> list[str]:
    return [sys.executable, "-m", "cheeseless", "--state-dir", str(case.state), *case.argv()]


# --- driving the real CLI --------------------------------------------------------------


def _run_detached(case: _Case) -> subprocess.CompletedProcess[bytes]:
    """The CLI in a new session: there is no controlling terminal to open."""
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        _command(case),
        input=PIPED,
        capture_output=True,
        cwd=case.repo.root,
        env=dict(os.environ),
        start_new_session=True,
        timeout=TIMEOUT,
        check=False,
    )


def _take_terminal() -> None:
    """In the child: lead a new session whose controlling terminal is stdout's pty."""
    os.setsid()
    fcntl.ioctl(1, termios.TIOCSCTTY, 0)


def _read_until(master: int, seen: bytearray, pattern: re.Pattern[bytes]) -> re.Match[bytes]:
    end = time.monotonic() + TIMEOUT
    while (match := pattern.search(seen)) is None:
        left = end - time.monotonic()
        assert left > 0, f"the code was never shown: {bytes(seen)!r}"
        if select.select([master], [], [], left)[0]:
            try:
                chunk = os.read(master, 4096)
            except OSError:  # EIO: the child closed the terminal without asking
                chunk = b""
            assert chunk, f"the terminal closed first: {bytes(seen)!r}"
            seen += chunk
    return match


def _drain(master: int, seen: bytearray) -> None:
    while select.select([master], [], [], 1.0)[0]:
        try:
            chunk = os.read(master, 4096)
        except OSError:  # EIO once the child has closed its side
            return
        if not chunk:
            return
        seen += chunk


def _run_attended(case: _Case, answer: Callable[[bytes], bytes]) -> tuple[int, str]:
    """The CLI on a pty that is its controlling terminal; the test is the operator."""
    master, slave = pty.openpty()
    seen = bytearray()
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            _command(case),
            stdin=subprocess.PIPE,
            stdout=slave,
            stderr=slave,
            cwd=case.repo.root,
            env=dict(os.environ),
            preexec_fn=_take_terminal,  # noqa: PLW1509 - must run in the child before exec
        )
        os.close(slave)
        try:
            assert proc.stdin is not None
            proc.stdin.write(PIPED)
            proc.stdin.close()
            code = _read_until(master, seen, CODE).group(1)
            os.write(master, answer(code) + b"\n")
            _drain(master, seen)
            rc = proc.wait(timeout=TIMEOUT)
            _drain(master, seen)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
    finally:
        os.close(master)
    return rc, seen.decode(errors="replace")


# --- 1. no controlling terminal: every attended decision is refused ---------------------


@pytest.mark.parametrize("name", sorted(CASES))
def test_without_a_controlling_terminal_the_decision_is_refused(name, toy_repo, tmp_path):
    case = CASES[name](toy_repo, tmp_path)
    assert not case.happened()
    done = _run_detached(case)
    out = (done.stdout + done.stderr).decode(errors="replace")
    assert done.returncode == 1, out
    assert "presence confirmation refused" in out, out
    assert "controlling terminal" in out, out
    assert "Traceback" not in out
    assert not case.happened()


# --- 2. positive control: the same CLI on a real terminal goes through ------------------
# Approve and recover-finish run the reference crosscheck in a real sandbox after the
# presence step; their success path is covered in-process with the Approving fake.


@pytest.mark.parametrize("name", ["auto-freeze-enable", "dispute-resolve", "promote-sensitive"])
def test_on_a_controlling_terminal_the_shown_code_lets_it_through(name, toy_repo, tmp_path):
    case = CASES[name](toy_repo, tmp_path)
    rc, seen = _run_attended(case, answer=lambda code: code)
    assert rc == 0, seen
    assert case.happened()
    assert any(w.NOTE in e.get("cause", "") for e in w._events(case.store)), seen


def test_on_a_controlling_terminal_a_wrong_code_is_refused(toy_repo, tmp_path):
    case = CASES["auto-freeze-enable"](toy_repo, tmp_path)
    rc, seen = _run_attended(case, answer=lambda code: code[::-1] + b"X")
    assert rc == 1, seen
    assert "did not match" in seen
    assert not case.happened()


# --- 3. the use cases refuse with a refusing port, whatever the inbound adapter ---------


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_use_case_refuses_with_a_refusing_port(name, toy_repo, tmp_path):
    case = CASES[name](toy_repo, tmp_path)
    proof = Refusing()
    with pytest.raises((AuthorError, PresenceRefused)) as caught:
        case.decide(proof)
    if isinstance(caught.value, AuthorError):
        assert caught.value.kind == "presence_refused"
    assert len(proof.asked) == 1
    assert not case.happened()


# --- 4. no bypass ----------------------------------------------------------------------


def test_the_cli_wires_the_terminal_challenge(toy_repo, tmp_path, monkeypatch):
    # The suite swaps the adapter for a refusing fake; put the real one back to check wiring.
    monkeypatch.setattr(bootstrap, "TtyPresence", TtyPresence)
    ctx = bootstrap.build_context(tmp_path / "state", toy_repo)
    assert type(ctx.presence) is TtyPresence


def test_in_process_cli_calls_in_this_suite_get_a_refusing_port():
    # Guard for whoever runs the tests from a terminal: nothing in-process prompts them.
    assert bootstrap.TtyPresence is not TtyPresence
    assert isinstance(bootstrap.TtyPresence(), Refusing)


def _options(parser) -> set[str]:
    found = set(parser._option_string_actions)
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        for child in choices.values() if isinstance(choices, dict) else ():
            if hasattr(child, "_option_string_actions"):
                found |= _options(child)
    return found


def test_no_cli_option_turns_the_challenge_off():
    words = ("presence", "tty", "challenge", "attend", "confirm")
    options = _options(build_parser())
    assert "--approve" in options  # the walk reached the subcommands
    assert not [opt for opt in options if any(word in opt for word in words)]


def test_no_environment_switch_reaches_the_challenge():
    switch = re.compile(r"CHEESELESS_\w*(PRESENCE|TTY|CHALLENGE|ATTEND)", re.IGNORECASE)
    for path in SRC.rglob("*.py"):
        assert not switch.search(path.read_text()), path
    for path in (
        SRC / "app" / "presence.py",
        SRC / "domain" / "presence.py",
        SRC / "ports" / "presence.py",
        *(SRC / "adapters" / "outbound" / "presence").rglob("*.py"),
    ):
        tree = ast.parse(path.read_text())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not {"environ", "getenv"} & (names | attrs), path


def test_only_the_composition_root_chooses_the_presence_port():
    """Every ``presence=`` in src forwards the bootstrap's port, builds the terminal
    adapter in the bootstrap, or is the unattended freeze carrying none."""
    allowed = {
        ("adapters/inbound/cli/main.py", "ctx.presence"),
        ("bootstrap.py", "TtyPresence()"),
        ("app/authoring.py", "None"),
    }
    seen = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.keyword) and node.arg == "presence":
                seen.add((path.relative_to(SRC).as_posix(), ast.unparse(node.value)))
    assert seen <= allowed, seen - allowed
    assert ("bootstrap.py", "TtyPresence()") in seen


def test_the_test_fakes_are_not_reachable_from_src():
    for path in SRC.rglob("*.py"):
        assert "presence_fakes" not in path.read_text(), path
