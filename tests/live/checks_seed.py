"""Release-gate check 13: seeding (P10-29)."""

from __future__ import annotations

from tests.live.checks_tokens import head_sha
from tests.live.model import CheckResult, Ctx, failed, guarded, passed
from tests.live.ref_ops import Precondition, Repo, need


@guarded("13")
def check_13_seeding(ctx: Ctx) -> CheckResult:
    ledger = Repo(ctx.client, ctx.config.ledger_repo)
    app = ctx.app_token()
    base = head_sha(ctx, ledger)
    sha = ledger.orphan(base, app, "cheeseless live probe 13")
    rows: list[str] = []
    back = ledger.call("GET", f"git/commits/{sha}", app)
    rows.append(f"13a unreferenced commit read-back: {back.meta()}")
    if back.status != 200 or back.get("sha") != sha:
        return failed("13", "a commit no ref points at is not readable right after the write", rows)

    fresh = ctx.config.fresh_repo
    name = fresh.split("/", 1)[1]
    token = ctx.minter.mint(fresh, permissions={"contents": "write"}, repositories=[name])
    judged = Repo(ctx.client, ctx.config.judged_repo)
    meta = need(judged.call("GET", "", ctx.minter.mint(judged.full)), {200}, "read the judged repo")
    repo_id = meta.get("id")
    if not isinstance(repo_id, int):
        raise Precondition("the judged repository has no numeric id")
    first = ctx.seeder.seed(fresh, token, repo_id=str(repo_id), lineage=f"live-{ctx.random_hex()}")
    rows.append(f"13b seed on the fresh repository: {first.detail}")
    if not first.ok or not first.root:
        return failed("13", "ci-budget-init did not succeed on a fresh ledger repository", rows)
    return passed(
        "13", "unreferenced commits are readable and seeding succeeds on a fresh repo", rows
    )
