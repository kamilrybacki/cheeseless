"""CI branch check: gate/ and spec/ PRs get different path rules (ADR 0009, R8).

Pure of I/O beyond the repository port. A spec/ PR also gets the author check (ADR 0018). Its
typed status (``pass``/``fail``/``unavailable``) is the only thing that leaves this job: the
problem text is never rendered anywhere a pull request can steer, and the status is not an
implementation verdict. Whether a ``fail`` blocks is decided later, by ``ci-gate``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cheeseless.app.authoring import AuthorCheckResult
from cheeseless.domain.authoring import AuthorStatus
from cheeseless.domain.ci_branch import BranchClass, classify_branch, decide_branch_paths
from cheeseless.ports.repository import Repository


@dataclass(frozen=True, slots=True)
class BranchOutcome:
    ok: bool
    reason: str
    branch_class: str
    rejected: tuple[str, ...] = ()
    advisory: tuple[str, ...] = ()
    author: AuthorStatus | None = None

    def public(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "branch_class": self.branch_class,
            "rejected": list(self.rejected),
            "advisory": list(self.advisory),
            "author": self.author.value if self.author else None,
        }


BRANCH_POLICY_CHECK = "cheeseless branch policy"


def check_payload(outcome: BranchOutcome, head_sha: str) -> dict[str, object]:
    """The check-run body for a branch-policy outcome (never the implementation verdict).

    The author check's notes and status are deliberately absent: they derive from PR content."""
    conclusion = "success" if outcome.ok else "failure"
    lines = [
        f"class: {outcome.branch_class}; result: {outcome.reason}.",
        "Path confinement only; this is not an implementation verdict.",
        *(f"rejected: {path!r}" for path in outcome.rejected),
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


def _author(
    author_check: Callable[[str], AuthorCheckResult] | None, head: str
) -> tuple[tuple[str, ...], AuthorStatus | None]:
    """The problem notes and typed status of the author check; ``(), None`` when it did not run."""
    if author_check is None:
        return (), None
    try:
        result = author_check(head)
    except Exception as exc:  # noqa: BLE001 - could not tell, which is never a finding
        return (f"author check unavailable: {type(exc).__name__}",), AuthorStatus.UNAVAILABLE
    return result.problems, result.status


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
    if cls is not BranchClass.SPEC:
        return BranchOutcome(True, "ok", label)
    notes, status = _author(author_check, head)
    return BranchOutcome(True, "ok", label, (), notes, status)
