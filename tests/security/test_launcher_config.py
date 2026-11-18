"""The model recorded for a launched session comes from operator config, never a label.

ADR 0015 point 4 / ADR 0016: both vendors must come from trusted config. Each refusal below
has a canary: the same setup minus the one fault succeeds, so the refusal is the check under
test and not an earlier failure.
"""

from pathlib import Path

import pytest

from cheeseless.adapters.outbound.config.launchers import TomlLauncherCatalog
from cheeseless.adapters.outbound.store.file_store import FileStore
from cheeseless.app.authoring import AuthorError, run_strength_review
from cheeseless.app.launching import resolve_launch
from cheeseless.domain.authoring import Proposal
from cheeseless.domain.launcher import LauncherError

PID = "a" * 64

CONFIG = """
[launchers.claude-sonnet]
command = ["claude", "--model", "claude-sonnet-5-5"]
model = "claude-sonnet-5-5"

[launchers.gpt-sol]
command = ["codex", "--model", "gpt-6.2-sol"]
model = "gpt-6.2-sol"

[launchers.claude-opus]
command = ["claude", "--model", "claude-opus-5"]
model = "claude-opus-5"
"""


def _config(tmp_path: Path, mode: int = 0o600) -> Path:
    directory = tmp_path / "cfg" / "cheeseless"
    directory.mkdir(parents=True)
    directory.chmod(0o700)
    path = directory / "launchers.toml"
    path.write_text(CONFIG)
    path.chmod(mode)
    return path


def _catalog(tmp_path: Path, mode: int = 0o600) -> TomlLauncherCatalog:
    return TomlLauncherCatalog(_config(tmp_path, mode))


def _store(tmp_path: Path) -> FileStore:
    store = FileStore(tmp_path / "state")
    store.save_proposal(
        Proposal(
            proposal_id=PID,
            lineage_id="b" * 64,
            baseline_commit="c" * 40,
            acceptance_tree="d" * 40,
            spec_digest="e" * 64,
        )
    )
    return store


def _launch(store: FileStore, catalog: TomlLauncherCatalog | None, name: str | None, role: str):
    """What the host does for a launch: resolve, then record the resolved model."""
    launcher = catalog.get(name) if catalog is not None and name is not None else None
    _command, model = resolve_launch(launcher, ["agent"] if launcher is None else [])
    store.register_session(PID, role, model)
    return model


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_a_config_others_can_write_is_refused(tmp_path: Path, mode: int) -> None:
    with pytest.raises(LauncherError, match="writable"):
        _catalog(tmp_path, mode).get("claude-sonnet")


def test_a_private_config_is_accepted(tmp_path: Path) -> None:
    """Canary: only the mode differs from the refused case above."""
    assert _catalog(tmp_path).get("claude-sonnet").model == "claude-sonnet-5-5"


def test_a_config_in_a_directory_others_can_write_is_refused(tmp_path: Path) -> None:
    path = _config(tmp_path)
    path.parent.chmod(0o777)
    with pytest.raises(LauncherError, match="directory"):
        TomlLauncherCatalog(path).get("claude-sonnet")


def test_a_symlinked_config_is_refused(tmp_path: Path) -> None:
    real = _config(tmp_path)
    link = real.parent / "link.toml"
    link.symlink_to(real)
    with pytest.raises(LauncherError):
        TomlLauncherCatalog(link).get("claude-sonnet")


def test_a_config_inside_the_repository_is_refused(tmp_path: Path) -> None:
    path = _config(tmp_path)
    with pytest.raises(LauncherError, match="outside the repository"):
        TomlLauncherCatalog(path, forbidden=(tmp_path,)).get("claude-sonnet")
    # Canary: the same file is fine when it is not under a forbidden root.
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert TomlLauncherCatalog(path, forbidden=(other,)).get("claude-sonnet")


def test_an_unknown_launcher_is_refused(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    with pytest.raises(LauncherError, match="no launcher named"):
        catalog.get("claude-sonet")
    assert catalog.get("claude-sonnet")  # canary: the file itself is fine


def test_a_launch_without_a_launcher_records_no_model_and_freeze_fails_closed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    catalog = _catalog(tmp_path)
    assert _launch(store, None, None, "author") == ""
    _launch(store, catalog, "gpt-sol", "reviewer")
    with pytest.raises(AuthorError) as caught:
        run_strength_review(store, PID, launch=lambda brief: (True, "gpt-6.2-sol"))
    assert caught.value.kind in {"author_unknown_launch", "strength_review_unknown_launch"}


def test_config_launched_author_and_reviewer_of_different_vendors_pass(tmp_path: Path) -> None:
    """Canary for the refusals around it: this exact setup gets through."""
    store = _store(tmp_path)
    catalog = _catalog(tmp_path)
    _launch(store, catalog, "claude-sonnet", "author")
    _launch(store, catalog, "gpt-sol", "reviewer")
    review = run_strength_review(store, PID, launch=lambda brief: (True, "gpt-6.2-sol"))
    assert (review.author_vendor, review.reviewer_vendor) == ("anthropic", "openai")


def test_same_vendor_is_refused_even_when_both_launched_from_config(tmp_path: Path) -> None:
    store = _store(tmp_path)
    catalog = _catalog(tmp_path)
    _launch(store, catalog, "claude-sonnet", "author")
    _launch(store, catalog, "claude-opus", "reviewer")
    with pytest.raises(AuthorError) as caught:
        run_strength_review(store, PID, launch=lambda brief: (True, "claude-opus-5"))
    assert caught.value.kind == "strength_review_not_independent"


def test_a_label_contradicting_the_config_is_refused(tmp_path: Path) -> None:
    launcher = _catalog(tmp_path).get("claude-sonnet")
    with pytest.raises(LauncherError, match="contradicts"):
        resolve_launch(launcher, [], "gpt-6.2-sol")
    # Canary: the matching label is accepted, and the configured model is what is recorded.
    assert resolve_launch(launcher, [], "claude-sonnet-5-5") == (
        list(launcher.command),
        "claude-sonnet-5-5",
    )


def test_a_caller_command_contradicting_the_config_is_refused(tmp_path: Path) -> None:
    launcher = _catalog(tmp_path).get("claude-sonnet")
    with pytest.raises(LauncherError, match="contradicts"):
        resolve_launch(launcher, ["codex", "--model", "gpt-6.2-sol"])
    assert resolve_launch(launcher, list(launcher.command))[1] == "claude-sonnet-5-5"


@pytest.mark.parametrize(
    "body",
    [
        '[launchers.x]\ncommand = []\nmodel = "m"\n',
        '[launchers.x]\ncommand = ["a", 1]\nmodel = "m"\n',
        '[launchers.x]\ncommand = ["a"]\nmodel = ""\n',
        '[launchers.x]\ncommand = ["a"]\n',
        '[launchers."Bad Name"]\ncommand = ["a"]\nmodel = "m"\n',
        "not toml [",
    ],
)
def test_a_malformed_entry_is_refused(tmp_path: Path, body: str) -> None:
    path = _config(tmp_path)
    path.write_text(body)
    path.chmod(0o600)
    with pytest.raises(LauncherError):
        TomlLauncherCatalog(path).get("x")
