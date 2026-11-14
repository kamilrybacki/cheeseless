"""Freeze enforces hidden sentinels and never echoes one in its diagnostics (ADR 0007).

* A hidden marker that the agent projection already shows, in its contents or its
  paths, raw or in a simple encoding, does not count as private and freeze refuses.
* Every artifact class with hidden files needs at least two distinct private markers.
* Diagnostics, including node ids printed by the real CLI, carry only marker digests.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.sandbox.bwrap import DEFAULT_BWRAP
from cheeseless.app.freeze import FreezeError, prepare
from cheeseless.domain.dlp import (
    ARTIFACT_EXPECTED,
    ARTIFACT_FIXTURES,
    ARTIFACT_NOTES,
    ARTIFACT_SEED,
    ARTIFACT_TESTS,
    MIN_SENTINELS_PER_CLASS,
    digest,
)
from tests.requirements import needs
from tests.toy import CANARY, TOY_FILES, commit_all, make_repo, write_files

FAMILIES = ("SENTINEL", "CANARY")
MARKER_FREE_TEST = "def test_stub(candidate):\n    pass\n"


class _ReachedSandbox(Exception):
    """Freeze passed the sentinel gate and went on to use the runner or sandbox."""


class _Boom:
    def __getattr__(self, name: str) -> object:
        raise _ReachedSandbox(name)


def _marker(family: str, suffix: str) -> str:
    return f"CHEESELESS-{family}-{suffix}"


def _base64_at(prefix_len: int) -> Callable[[str], str]:
    def encode(token: str) -> str:
        blob = b"z" * (prefix_len + 6) + token.encode() + b"#tail of a longer blob"
        return base64.b64encode(blob).decode()

    return encode


def _wrapped_base64(line_end: str) -> Callable[[str], str]:
    def encode(token: str) -> str:
        blob = b"x" * 48 + token.encode() + b"!tail"
        return base64.encodebytes(blob).decode().replace("\n", line_end)

    return encode


FORMS: dict[str, Callable[[str], str]] = {
    "raw": lambda token: token,
    "lowercase": lambda token: token.lower(),
    "reversed": lambda token: token[::-1],
    "hex": lambda token: token.encode().hex(),
    "hex-upper": lambda token: token.encode().hex().upper(),
    "base64-align0": _base64_at(0),
    "base64-align1": _base64_at(1),
    "base64-align2": _base64_at(2),
    "base64-wrapped-lf": _wrapped_base64("\n"),
    "base64-wrapped-crlf": _wrapped_base64("\r\n"),
}


def _assert_no_marker(message: str, *markers: str) -> None:
    lowered = message.lower()
    for marker in markers:
        assert marker not in message
        assert marker.lower() not in lowered
    assert "cheeseless-sentinel" not in lowered
    assert "cheeseless-canary" not in lowered


def _hidden_with_three_private(family: str, exposed: str) -> dict[str, str]:
    files = dict(TOY_FILES)
    files["acceptance/hidden/test_normalize.py"] = (
        f"# {_marker(family, 'priv01abc')}\n# {exposed}\n" + MARKER_FREE_TEST
    )
    files["acceptance/hidden/test_score.py"] = (
        f"# {_marker(family, 'priv02abc')}\n# {_marker(family, 'priv03abc')}\n" + MARKER_FREE_TEST
    )
    return files


def _refusal(root: Path) -> str:
    with pytest.raises(FreezeError) as caught:
        prepare(GitRepository.discover(root), _Boom(), _Boom())
    return str(caught.value)


@pytest.mark.parametrize("form", sorted(FORMS))
@pytest.mark.parametrize("surface", ["contents", "path"])
@pytest.mark.parametrize("family", FAMILIES)
def test_freeze_refuses_a_projection_visible_marker_in_any_form(
    tmp_path: Path, family: str, surface: str, form: str
) -> None:
    exposed = _marker(family, "pub02abc")
    root = make_repo(tmp_path / "toy", _hidden_with_three_private(family, exposed))
    encoded = FORMS[form](exposed)
    if surface == "contents":
        write_files(root, {"src/toy/leaked.txt": f"blob:\n{encoded}\nend\n"})
    else:
        write_files(root, {f"src/leak_{encoded}.txt": "\n"})
    commit_all(root, "projection leak")

    message = _refusal(root)

    assert "visible in the projection" in message
    assert digest(exposed.encode()) in message
    _assert_no_marker(message, exposed, _marker(family, "priv01abc"))


def test_freeze_refuses_the_toy_canary_in_the_projection_without_echoing_it(
    tmp_path: Path,
) -> None:
    files = dict(TOY_FILES)
    files["acceptance/hidden/test_normalize.py"] = (
        f"# {CANARY}\n# {_marker('SENTINEL', 'extra01abc')}\n"
        f"# {_marker('SENTINEL', 'extra02abc')}\n" + MARKER_FREE_TEST
    )
    root = make_repo(tmp_path / "toy", files)
    write_files(root, {"src/toy/leaked.py": f"TOKEN = {CANARY!r}\n"})
    commit_all(root, "canary leak")

    message = _refusal(root)

    assert "visible in the projection" in message
    assert digest(CANARY.encode()) in message
    _assert_no_marker(message, CANARY)


CLASS_PATHS = {
    ARTIFACT_TESTS: "acceptance/hidden/test_extra.py",
    ARTIFACT_FIXTURES: "acceptance/hidden/fixtures/input.json",
    ARTIFACT_EXPECTED: "acceptance/hidden/expected/out.json",
    ARTIFACT_SEED: "acceptance/hidden/seed.json",
    ARTIFACT_NOTES: "acceptance/hidden/notes.md",
}


def _suite_with_class_markers(cls: str, count: int) -> dict[str, str]:
    files = dict(TOY_FILES)
    if cls == ARTIFACT_TESTS:
        files["acceptance/hidden/test_normalize.py"] = MARKER_FREE_TEST
        files["acceptance/hidden/test_score.py"] = MARKER_FREE_TEST
    markers = "\n".join(f"# {_marker('SENTINEL', f'{cls}{i:02d}abc')}" for i in range(count))
    files[CLASS_PATHS[cls]] = markers + "\n"
    return files


@pytest.mark.parametrize("cls", sorted(CLASS_PATHS))
def test_freeze_refuses_a_class_with_a_single_marker(tmp_path: Path, cls: str) -> None:
    root = make_repo(tmp_path / "toy", _suite_with_class_markers(cls, 1))

    message = _refusal(root)

    assert f"artifact class {cls!r} has 1 distinct sentinel token(s)" in message
    assert f"at least {MIN_SENTINELS_PER_CLASS}" in message
    _assert_no_marker(message)


@pytest.mark.parametrize("cls", sorted(CLASS_PATHS))
def test_freeze_accepts_a_class_with_two_distinct_markers(tmp_path: Path, cls: str) -> None:
    assert MIN_SENTINELS_PER_CLASS == 2
    root = make_repo(tmp_path / "toy", _suite_with_class_markers(cls, 2))

    with pytest.raises(_ReachedSandbox):
        prepare(GitRepository.discover(root), _Boom(), _Boom())


NODE_MARKER = "CHEESELESS-SENTINEL-nodeid9x"

HIDDEN_WITH_MARKED_NODE_IDS = f"""\
# {CANARY}
import pytest

pytestmark = pytest.mark.criterion("score")


@pytest.mark.parametrize("case", [1], ids=["{NODE_MARKER}"])
def test_marked_skip(candidate, case):
    pytest.skip("skipped on purpose")


@pytest.mark.criterion("not-a-criterion")
@pytest.mark.parametrize("case", [2], ids=["{NODE_MARKER}"])
def test_marked_two_criteria(candidate, case):
    assert False
"""


@needs(
    os.access(DEFAULT_BWRAP, os.X_OK) and os.access("/usr/bin/python3", os.X_OK),
    "needs bubblewrap and /usr/bin/python3",
)
def test_cli_freeze_redacts_markers_in_hidden_node_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cheeseless.bootstrap import main  # noqa: PLC0415 - keeps collection light

    files = dict(TOY_FILES)
    files["acceptance/hidden/test_score.py"] = HIDDEN_WITH_MARKED_NODE_IDS
    root = make_repo(tmp_path / "toy", files)
    state = tmp_path / "state"
    monkeypatch.chdir(root)

    rc = main(["--state-dir", str(state), "freeze", "--yes"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "freeze failed" in captured.err
    assert "skip" in captured.err
    assert "must name exactly one criterion" in captured.err
    assert f"<sentinel digest {digest(NODE_MARKER.encode())}>" in captured.err
    assert "Traceback" not in captured.err
    _assert_no_marker(captured.err + captured.out, NODE_MARKER, CANARY)
