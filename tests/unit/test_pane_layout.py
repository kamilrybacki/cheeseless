"""Role pane layout (S6): one socket per lineage and role, inside the state dir, no digest."""

import json
from pathlib import Path

import pytest

from cheeseless import pane_layout
from cheeseless.pane_layout import PaneLayoutError, PaneMode, PaneRole


def test_the_roles_are_closed_and_each_has_one_mode():
    assert {r.value for r in PaneRole} == {"test-writer", "reference", "coder"}
    assert pane_layout.mode_of(PaneRole.TEST_WRITER) is PaneMode.INTERACTIVE
    assert pane_layout.mode_of(PaneRole.REFERENCE) is PaneMode.VIEW
    assert pane_layout.mode_of(PaneRole.CODER) is PaneMode.VIEW


def test_the_socket_lives_under_the_state_dir_and_names_only_lineage_and_role(tmp_path):
    path = pane_layout.socket_path(tmp_path, "lin-1", PaneRole.CODER)
    assert path.is_relative_to(tmp_path)
    assert path.relative_to(tmp_path).parts == ("cheeseless", "panes", "lin-1", "coder.sock")


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "A", "x" * 65, "..", ".hidden/../.."])
def test_a_bad_lineage_is_refused(tmp_path, bad):
    with pytest.raises((ValueError, PaneLayoutError)):
        pane_layout.socket_path(tmp_path, bad, PaneRole.CODER)


def test_a_state_dir_with_a_symlink_that_escapes_is_refused(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (state / "cheeseless").symlink_to(outside)
    with pytest.raises(PaneLayoutError, match="outside the state dir"):
        pane_layout.socket_path(state, "lin-1", PaneRole.CODER)


def test_a_path_too_long_for_a_unix_socket_is_a_clear_error(tmp_path):
    deep = tmp_path / ("d" * 90)
    with pytest.raises(PaneLayoutError, match="too long"):
        pane_layout.socket_path(deep, "lin-1", PaneRole.CODER)


def test_the_panes_event_is_closed_and_holds_roles_modes_and_paths_only(tmp_path):
    event = json.loads(pane_layout.panes_event_line(tmp_path, "lin-1", list(PaneRole)))
    assert set(event) == {"event", "schema", "lineage", "panes"}
    assert event["event"] == "panes" and event["schema"] == 1 and event["lineage"] == "lin-1"
    assert [p["role"] for p in event["panes"]] == ["test-writer", "reference", "coder"]
    for pane in event["panes"]:
        assert set(pane) == {"role", "mode", "socket"}
        assert pane["mode"] in {"view", "interactive"}
        assert Path(pane["socket"]).is_relative_to(tmp_path)
