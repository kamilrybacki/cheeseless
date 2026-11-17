"""CI branch check: gate/ and spec/ PRs get different path rules (ADR 0009, R8).

Pure of I/O beyond the repository port. A spec/ PR also gets the advisory author check, whose
problems are reported but never fail the job and never form an implementation verdict.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cheeseless.app.authoring import AuthorCheckResult
from cheeseless.domain.ci_branch import BranchClass, classify_branch, decide_branch_paths
from cheeseless.ports.repository import Repository


@dataclass(frozen=True, slots=True)
class BranchOutcome:
    ok: bool
    reason: str
    branch_class: str
    rejected: tuple[str, ...] = ()
    advisory: tuple[str, ...] = ()

    def public(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "branch_class": self.branch_class,
            "rejected": list(self.rejected),
            "advisory": list(self.advisory),
        }


BRANCH_POLICY_CHECK = "cheeseless branch policy"


def check_payload(outcome: BranchOutcome, head_sha: str) -> dict[str, object]:
    """The check-run body for a branch-policy outcome (never the implementation verdict)."""
    conclusion = "success" if outcome.ok else "failure"
    lines = [
        f"class: {outcome.branch_class}; result: {outcome.reason}.",
        "Path confinement only; this is not an implementation verdict.",
        *(f"rejected: {path!r}" for path in outcome.rejected),
        *(f"advisory: {note}" for note in outcome.advisory),
    ]
    return {
        "name": BRANCH_POLICY_CHECK,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": {
            "title": f"{BRANCH_POLICY_CHECK}: {conclusion}",
            "summary": "\n".join(lines),
        },
    }


def _advisory(
    author_check: Callable[[str], AuthorCheckResult] | None, head: str
) -> tuple[str, ...]:
    if author_check is None:
        return ()
    try:
        return author_check(head).problems
    except Exception as exc:  # noqa: BLE001 - advisory only, never a verdict
        return (f"author check unavailable: {type(exc).__name__}",)


def check_branch(
    repo: Repository,
    branch: str,
    base: str,
    head: str,
    *,
    author_check: Callable[[str], AuthorCheckResult] | None = None,
) -> BranchOutcome:
    cls = classify_branch(branch)
    label = cls.value if cls else "unknown"
    if cls is None:
        return BranchOutcome(False, "unknown_branch_class", label)
    fork = repo.tree_of(repo.merge_base(base, head))
    verdict = decide_branch_paths(cls, repo.diff_modes(fork, repo.tree_of(head)))
    if not verdict.ok:
        return BranchOutcome(False, verdict.reason, label, verdict.rejected)
    advisory = _advisory(author_check, head) if cls is BranchClass.SPEC else ()
    return BranchOutcome(True, "ok", label, (), advisory)
