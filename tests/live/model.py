"""Result model and shared context for the live release-gate checks."""

from __future__ import annotations

import enum
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from tests.live.config import LiveConfig
from tests.live.github_client import ClientError, GitHubClient, Response, Token
from tests.live.ref_ops import Precondition
from tests.live.seeding_port import SeedPort
from tests.live.tokens import TokenMinter


class Outcome(enum.Enum):
    PASS = "pass"  # noqa: S105 - an outcome label, not a credential
    FAIL = "fail"
    PRECONDITION_MISSING = "precondition missing"
    NOT_APPLICABLE = "not applicable"


@dataclass(frozen=True)
class CheckResult:
    check: str
    outcome: Outcome
    detail: str
    evidence: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.outcome is Outcome.PASS

    def render(self) -> str:
        lines = [f"[{self.check}] {self.outcome.value}: {self.detail}"]
        lines.extend(f"  - {line}" for line in self.evidence)
        return "\n".join(lines)


def passed(check: str, detail: str, evidence: Sequence[str] = ()) -> CheckResult:
    return CheckResult(check, Outcome.PASS, detail, tuple(evidence))


def failed(check: str, detail: str, evidence: Sequence[str] = ()) -> CheckResult:
    return CheckResult(check, Outcome.FAIL, detail, tuple(evidence))


def precondition_missing(check: str, detail: str, evidence: Sequence[str] = ()) -> CheckResult:
    return CheckResult(check, Outcome.PRECONDITION_MISSING, detail, tuple(evidence))


def not_applicable(check: str, detail: str) -> CheckResult:
    return CheckResult(check, Outcome.NOT_APPLICABLE, detail)


def run_threads[T](calls: Sequence[Callable[[], T]]) -> list[T]:
    """Run every call on its own thread, all released together."""
    with ThreadPoolExecutor(max_workers=max(1, len(calls))) as pool:
        futures = [pool.submit(call) for call in calls]
        return [f.result() for f in futures]


@dataclass
class Ctx:
    """Everything a check needs; tests inject fakes for every port."""

    config: LiveConfig
    client: GitHubClient
    minter: TokenMinter
    seeder: SeedPort
    sleep: Callable[[float], None]
    parallel: Callable[[Sequence[Callable[[], Response]]], list[Response]] = run_threads
    random_hex: Callable[[], str] = lambda: "0" * 8  # noqa: E731 - replaced by the runner
    poll_seconds: float = 300.0
    _tokens: dict[str, Token] = field(default_factory=dict)

    def app_token(self) -> Token:
        """The judge App acting with its full permissions on the ledger repository."""
        return self._cached("app", lambda: self.minter.mint(self.config.ledger_repo))

    def budget_token(self) -> Token:
        """The workflow's budget token: Contents write on the ledger repository only."""
        return self._cached(
            "budget",
            lambda: self.minter.mint(
                self.config.ledger_repo,
                permissions={"contents": "write"},
                repositories=[self.config.ledger_name],
            ),
        )

    def verdict_token(self) -> Token:
        """The workflow's checks token: Checks write, nothing else."""
        return self._cached(
            "verdict",
            lambda: self.minter.mint(self.config.judged_repo, permissions={"checks": "write"}),
        )

    def _cached(self, key: str, make: Callable[[], Token]) -> Token:
        if key not in self._tokens:
            self._tokens[key] = make()
        return self._tokens[key]


def guarded(check: str) -> Callable[[Callable[[Ctx], CheckResult]], Callable[[Ctx], CheckResult]]:
    """A missing prerequisite or an unusable credential is reported, never swallowed as a pass."""

    def wrap(fn: Callable[[Ctx], CheckResult]) -> Callable[[Ctx], CheckResult]:
        def run(ctx: Ctx) -> CheckResult:
            try:
                return fn(ctx)
            except Precondition as err:
                return precondition_missing(check, str(err))
            except ClientError as err:
                return precondition_missing(check, f"GitHub is unusable here: {err}")

        run.__name__ = fn.__name__
        run.__doc__ = fn.__doc__
        return run

    return wrap
