"""The 13 release-gate checks of planning/research/ci-budget-counter.md section 6.

Run: CHEESELESS_LIVE_GITHUB=1 uv run --frozen pytest tests/live -m live_github --no-cov
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import pytest

from tests.live import checks_counter as counter
from tests.live import checks_seed as seed
from tests.live import checks_tokens as tokens
from tests.live import checks_workflow as workflow
from tests.live.model import CheckResult, Ctx, Outcome
from tests.live.runtime import build_ctx

pytestmark = pytest.mark.live_github


@pytest.fixture(scope="session")
def ctx() -> Iterator[Ctx]:
    context, minter = build_ctx(os.environ)
    try:
        yield context
    finally:
        minter.close()


def verdict(result: CheckResult) -> None:
    """Only a pass passes. A missing precondition is a failure, never a silent skip."""
    print(result.render())
    if result.outcome is Outcome.NOT_APPLICABLE:
        pytest.skip(result.detail)
    assert result.outcome is Outcome.PASS, result.render()


def run(check: Callable[[Ctx], CheckResult], ctx: Ctx) -> None:
    verdict(check(ctx))


def test_check_01_rulesets_available(ctx: Ctx) -> None:
    run(counter.check_01_rulesets_available, ctx)


def test_check_02_bypass_per_ruleset(ctx: Ctx) -> None:
    run(counter.check_02_bypass_per_ruleset, ctx)


def test_check_03_ruleset_matrix(ctx: Ctx) -> None:
    run(counter.check_03_ruleset_matrix, ctx)


def test_check_04_concurrent_writers(ctx: Ctx) -> None:
    run(counter.check_04_concurrent_writers, ctx)


def test_check_05_read_after_update(ctx: Ctx) -> None:
    run(counter.check_05_read_after_update, ctx)


def test_check_06_budget_token_scope(ctx: Ctx) -> None:
    run(tokens.check_06_budget_token_scope, ctx)


def test_check_07_verdict_token_scope(ctx: Ctx) -> None:
    run(tokens.check_07_verdict_token_scope, ctx)


def test_check_08_same_repo_protection(ctx: Ctx) -> None:
    run(tokens.check_08_same_repo_protection, ctx)


def test_check_09_branch_workflow_blind(ctx: Ctx) -> None:
    run(workflow.check_09_branch_workflow_blind, ctx)


def test_check_10_judge_reads_secret(ctx: Ctx) -> None:
    run(workflow.check_10_judge_reads_secret, ctx)


def test_check_11_required_check_source(ctx: Ctx) -> None:
    run(workflow.check_11_required_check_source, ctx)


def test_check_12_creation_rejected(ctx: Ctx) -> None:
    run(counter.check_12_creation_rejected, ctx)


def test_check_13_seeding(ctx: Ctx) -> None:
    run(seed.check_13_seeding, ctx)
