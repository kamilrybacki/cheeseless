"""CI routing: decide from the live pull request which checks a head SHA gets.

Only a ``gate/`` PR reaches the implementation judge. ``spec/`` and unknown classes get the
branch-policy check only. Everything is derived from the PR as the API returns it (never from
the workflow event payload), and a PR that cannot be verified yields no route at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cheeseless.domain.ci_branch import BranchClass, classify_branch


@dataclass(frozen=True, slots=True)
class Route:
    ok: bool
    reason: str
    number: int = 0
    base_sha: str = ""
    branch: str = ""
    branch_class: str = ""
    run_judge: bool = False

    def public(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "number": self.number,
            "base_sha": self.base_sha,
            "branch": self.branch,
            "branch_class": self.branch_class,
            "run_judge": self.run_judge,
        }


def _refuse(reason: str) -> Route:
    return Route(False, reason)


def _get(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, Mapping):
            return None
        obj = obj.get(key)
    return obj


def resolve_route(prs: Sequence[Any], head_sha: str, default_branch: str, repo: str) -> Route:
    """Validate the single open PR for ``head_sha`` (base, head, repo, class) and route it.

    A PR whose head lives in another repository (a fork) is refused: its branch name is
    attacker-chosen and must never buy a judge run.
    """
    open_prs = [p for p in prs if _get(p, "state") == "open"]
    if len(open_prs) != 1:
        return _refuse("expected_exactly_one_open_pr")
    pr = open_prs[0]
    number, base_sha = _get(pr, "number"), _get(pr, "base", "sha")
    branch = _get(pr, "head", "ref")
    if _get(pr, "base", "ref") != default_branch:
        return _refuse("base_is_not_the_default_branch")
    if _get(pr, "head", "sha") != head_sha:
        return _refuse("head_moved")
    if not repo or _get(pr, "head", "repo", "full_name") != repo:
        return _refuse("head_repo_is_not_this_repository")
    if _get(pr, "base", "repo", "full_name") != repo:
        return _refuse("base_repo_is_not_this_repository")
    if not (isinstance(number, int) and isinstance(base_sha, str) and isinstance(branch, str)):
        return _refuse("malformed_pull_request")
    cls = classify_branch(branch)
    return Route(
        True,
        "ok",
        number,
        base_sha,
        branch,
        cls.value if cls else "unknown",
        cls is BranchClass.GATE,
    )
