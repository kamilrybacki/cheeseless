"""Which changed paths may be promoted (Q17, Q56).

Order: the hard denylist always wins, then sensitive files (operator approval), then
the allowlist; anything else is rejected. Non-file entries are rejected.
"""

import enum
import fnmatch
import posixpath
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

REGULAR_MODES = frozenset({"100644", "100755"})
DELETED_MODE = "000000"

_DENY_PREFIXES = ("acceptance/", ".cheeseless/", ".github/workflows/")
_DENY_EXACT = frozenset({"acceptance", ".cheeseless", ".gitmodules"})
_ALLOW_PREFIXES = ("src/", "tests/", "docs/")
_SENSITIVE_NAMES_ANYWHERE = frozenset(
    {"conftest.py", ".gitattributes", "AGENTS.md", "CLAUDE.md", "sitecustomize.py"}
)
_SENSITIVE_TOP = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Makefile",
        "uv.lock",
        "poetry.lock",
        "Pipfile",
        "Pipfile.lock",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "Cargo.lock",
        "Cargo.toml",
        "go.sum",
        "go.mod",
        "tox.ini",
        "noxfile.py",
    }
)
_SENSITIVE_TOP_GLOBS = ("requirements*.txt", "constraints*.txt")


class PathClass(enum.Enum):
    ALLOWED = "allowed"
    SENSITIVE = "sensitive"
    REJECTED = "rejected"


def _valid(path: str) -> bool:
    return (
        bool(path)
        and path.isprintable()
        and not path.startswith("/")
        and "\\" not in path
        and posixpath.normpath(path) == path
        and ".git" not in path.lower().split("/")
        and ".." not in path.split("/")
    )


def is_protected(path: str) -> bool:
    """True for paths that protect the hidden tests; the CI judge fails any change to them."""
    return path in _DENY_EXACT or path.startswith(_DENY_PREFIXES)


def classify(path: str) -> PathClass:
    if not _valid(path):
        return PathClass.REJECTED
    if is_protected(path):
        return PathClass.REJECTED
    name = posixpath.basename(path)
    if name in _SENSITIVE_NAMES_ANYWHERE or path.startswith(".github/"):
        return PathClass.SENSITIVE
    if "/" not in path and (
        path in _SENSITIVE_TOP or any(fnmatch.fnmatchcase(path, g) for g in _SENSITIVE_TOP_GLOBS)
    ):
        return PathClass.SENSITIVE
    if path.startswith(_ALLOW_PREFIXES):
        return PathClass.ALLOWED
    if "/" not in path and name.startswith("README"):
        return PathClass.ALLOWED
    return PathClass.REJECTED


@dataclass(frozen=True, slots=True)
class PromoteDecision:
    rejected: tuple[str, ...]
    needs_approval: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.rejected and not self.needs_approval


def decide_promote(changes: Mapping[str, str], approved: Iterable[str] = ()) -> PromoteDecision:
    """``changes`` maps path → new git mode (``000000`` for a deletion)."""
    approvals = set(approved)
    rejected: list[str] = []
    needs: list[str] = []
    for path, mode in sorted(changes.items()):
        cls = classify(path)
        if cls is PathClass.REJECTED or (mode not in REGULAR_MODES and mode != DELETED_MODE):
            rejected.append(path)
        elif cls is PathClass.SENSITIVE and path not in approvals:
            needs.append(path)
    return PromoteDecision(tuple(rejected), tuple(needs))
