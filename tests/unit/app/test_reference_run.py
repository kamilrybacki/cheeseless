"""ADR 0014: the host-side lifecycle moves around a reference attempt."""

from pathlib import Path

import pytest

from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.app.reference_run import (
    cancel_attempt,
    choose_mode,
    complete_attempt,
    fail_attempt,
    reap_attempts,
    stop_attempt,
)
from cheeseless.domain.reference_attempt import (
    ReferenceAttempt,
    ReferenceMode,
    ReferenceState,
)

S = ReferenceState
SESSION = "0123456789abcdef0123456789abcdef"
TREE = "b" * 40


def _attempt(attempt_id: str = "att-1", state: S = S.RUNNING, **over: object) -> ReferenceAttempt:
    fields: dict[str, object] = {
        "attempt_id": attempt_id,
        "session_id": SESSION,
        "generation": 0,
        "spec_digest": "a" * 64,
        "mode": ReferenceMode.TUI,
        "attached": False,
        "state": state,
        "lineage_id": "toy",
    }
    return ReferenceAttempt(**{**fields, **over})  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(tmp_path / "state")


@pytest.mark.parametrize(
    ("headless", "in_tmux", "mode"),
    [
        (False, True, ReferenceMode.TUI),
        (False, False, ReferenceMode.HEADLESS),
        (True, True, ReferenceMode.HEADLESS),
        (True, False, ReferenceMode.HEADLESS),
    ],
)
def test_the_mode_is_chosen_before_launch(headless, in_tmux, mode):
    assert choose_mode(headless=headless, in_tmux=in_tmux) is mode


def test_fail_moves_running_or_stopping_and_returns_none_on_a_lost_race(store):
    store.save_reference_attempt(_attempt(state=S.STOPPING))
    failed = fail_attempt(store, store.reference_attempt("att-1"))
    assert failed is not None and failed.state is S.FAILED
    assert fail_attempt(store, failed) is None


@pytest.mark.parametrize("state", [S.DONE, S.FAILED, S.CANCELLED])
def test_fail_refuses_a_terminal_state(store, state):
    store.save_reference_attempt(_attempt(state=state))
    assert fail_attempt(store, store.reference_attempt("att-1")) is None


def test_cancel_moves_a_running_attempt(store):
    store.save_reference_attempt(_attempt())
    cancelled = cancel_attempt(store, store.reference_attempt("att-1"))
    assert cancelled is not None and cancelled.state is S.CANCELLED


def test_stop_then_complete_binds_the_tree(store):
    store.save_reference_attempt(_attempt())
    stopped = stop_attempt(store, store.reference_attempt("att-1"))
    assert stopped is not None and stopped.state is S.STOPPING
    done = complete_attempt(store, stopped, TREE)
    assert done is not None and done.state is S.DONE and done.result_tree == TREE
    assert complete_attempt(store, done, TREE) is None


def test_only_the_winner_of_running_to_stopping_completes(store):
    store.save_reference_attempt(_attempt())
    first = stop_attempt(store, store.reference_attempt("att-1"))
    second = stop_attempt(store, store.reference_attempt("att-1"))
    assert first is not None and second is None
    assert complete_attempt(store, first, TREE) is not None


def test_reaping_fails_stale_attempts_and_kills_their_panes(store):
    store.save_reference_attempt(_attempt("att-live", pane_id="%1"))
    store.save_reference_attempt(_attempt("att-stopping", state=S.STOPPING, pane_id="%2"))
    store.save_reference_attempt(_attempt("att-done", state=S.DONE, pane_id="%3"))
    closed: list[str] = []
    reaped = reap_attempts(store, "toy", closed.append)
    assert {a.attempt_id for a in reaped} == {"att-live", "att-stopping"}
    assert {a.state for a in reaped} == {S.FAILED}
    assert sorted(closed) == ["%1", "%2"]  # a terminal pane id may have been reused


def test_reaping_never_touches_another_lineage(store):
    store.save_reference_attempt(_attempt("att-other", lineage_id="other", pane_id="%4"))
    closed: list[str] = []
    assert reap_attempts(store, "toy", closed.append) == ()
    assert closed == []
    assert store.reference_attempt("att-other").state is S.RUNNING
