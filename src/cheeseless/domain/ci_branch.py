"""CI branch classes: gate/ (implementer) and spec/ (author), and what each may change."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from cheeseless.domain.promote_policy import decide_author_promote

_HEADS = "refs/heads/"


class BranchClass(StrEnum):
    GATE = "gate"
    SPEC = "spec"


@dataclass(frozen=True, slots=True)
class BranchVerdict:
    ok: bool
    reason: str
    rejected: tuple[str, ...] = ()


def classify_branch(name: str) -> BranchClass | None:
    """The class of a PR head branch, or ``None`` for anything that is not gate/ or spec/."""
    short = name.removeprefix(_HEADS)
    for cls in BranchClass:
        rest = short.removeprefix(f"{cls.value}/")
        if rest != short and _is_simple(rest):
            return cls
    return None


def _is_simple(rest: str) -> bool:
    return (
        bool(rest)
        and rest.isprintable()
        and " " not in rest
        and all(part not in {"", ".", ".."} for part in rest.split("/"))
    )


def decide_branch_paths(cls: BranchClass | None, changes: Mapping[str, str]) -> BranchVerdict:
    """gate/ PRs never touch acceptance/; spec/ PRs touch nothing but acceptance/."""
    if cls is None:
        return BranchVerdict(False, "unknown_branch_class")
    if cls is BranchClass.GATE:
        bad = tuple(sorted(p for p in changes if p == "acceptance" or p.startswith("acceptance/")))
        return BranchVerdict(not bad, "ok" if not bad else "acceptance_in_gate_pr", bad)
    decision = decide_author_promote(changes)
    reason = "ok" if decision.ok else "outside_author_paths"
    return BranchVerdict(decision.ok, reason, decision.rejected)
