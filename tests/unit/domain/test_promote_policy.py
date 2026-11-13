"""Promote path filter (Q17, Q56)."""

import pytest

from cheeseless.domain.promote_policy import PathClass, classify, decide_promote


@pytest.mark.parametrize(
    "path",
    [
        "src/toy/core.py",
        "tests/test_core.py",
        "docs/usage.md",
        "README.md",
        "README",
    ],
)
def test_allowed(path: str) -> None:
    assert classify(path) is PathClass.ALLOWED


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "requirements-dev.txt",
        "uv.lock",
        "src/conftest.py",
        "tests/conftest.py",
        "src/toy/sitecustomize.py",
        ".gitattributes",
        "src/.gitattributes",
        "AGENTS.md",
        ".github/dependabot.yml",
    ],
)
def test_sensitive(path: str) -> None:
    assert classify(path) is PathClass.SENSITIVE


@pytest.mark.parametrize(
    "path",
    [
        "acceptance",
        "acceptance/test_hidden.py",
        "acceptance/contract/criteria.yaml",
        ".cheeseless/config.yaml",
        ".github/workflows/ci.yml",
        ".gitmodules",
        "src/.git/config",
        "src/.GIT/hooks/post-checkout",
        "/etc/passwd",
        "src/../acceptance/x.py",
        "src//x.py",
        "src\\x.py",
        "./src/x.py",
        "",
        "setup.sh",
        "docs",
        "lib/x.py",
        "src/README\nx",
    ],
)
def test_rejected(path: str) -> None:
    assert classify(path) is PathClass.REJECTED


def test_denylist_wins_over_approval() -> None:
    decision = decide_promote({"acceptance/test_x.py": "100644"}, approved=["acceptance/test_x.py"])
    assert decision.rejected == ("acceptance/test_x.py",)
    assert not decision.ok


def test_sensitive_files_need_approval() -> None:
    changes = {"pyproject.toml": "100644", "src/x.py": "100644"}
    assert decide_promote(changes).needs_approval == ("pyproject.toml",)
    assert decide_promote(changes, approved=["pyproject.toml"]).ok


@pytest.mark.parametrize("mode", ["120000", "160000", "040000", "100664"])
def test_non_regular_entries_are_rejected(mode: str) -> None:
    assert decide_promote({"src/link": mode}).rejected == ("src/link",)


def test_deletions_and_executables_are_fine() -> None:
    assert decide_promote({"src/old.py": "000000", "src/run.py": "100755"}).ok


def test_is_protected_covers_deny_list():
    from cheeseless.domain.promote_policy import is_protected

    for p in (
        "acceptance",
        "acceptance/hidden/t.py",
        ".cheeseless/x",
        ".github/workflows/ci.yml",
        ".gitmodules",
    ):
        assert is_protected(p), p
    for p in ("src/a.py", "docs/x.md", ".github/CODEOWNERS", "acceptances/x"):
        assert not is_protected(p), p
