"""Release-gate checks 6-8: what the two workflow tokens and the App cannot do."""

from __future__ import annotations

import base64

from tests.live.github_client import Response, Token
from tests.live.model import CheckResult, Ctx, failed, guarded, not_applicable, passed
from tests.live.ref_ops import (
    SCOPE_DENIED,
    Precondition,
    Repo,
    need,
    protection_rejection,
    sha_of,
    write_control,
)


def put_contents(repo: Repo, token: Token, path: str, branch: str | None = None) -> Response:
    body: dict[str, object] = {
        "message": "cheeseless live probe",
        "content": base64.b64encode(b"probe\n").decode(),
    }
    if branch is not None:
        body["branch"] = branch
    return repo.call("PUT", f"contents/{path}", token, body)


def head_sha(ctx: Ctx, repo: Repo) -> str:
    app = ctx.minter.mint(repo.full)
    ref = repo.call("GET", f"git/ref/heads/{repo.default_branch(app)}", app)
    return sha_of(need(ref, {200}, f"read the head of {repo.full}"), "object", "sha")


def post_check_run(repo: Repo, token: Token, sha: str, name: str) -> Response:
    return repo.call(
        "POST",
        "check-runs",
        token,
        {"name": name, "head_sha": sha, "status": "completed", "conclusion": "success"},
    )


def _denied(label: str, resp: Response, rows: list[str]) -> bool:
    if resp.status == 401:
        raise Precondition("credential authentication failed")
    ok = resp.status in SCOPE_DENIED
    rows.append(f"{label}: {resp.meta()}: {'denied' if ok else 'NOT DENIED'}")
    return ok


@guarded("6")
def check_06_budget_token_scope(ctx: Ctx) -> CheckResult:
    judged = Repo(ctx.client, ctx.config.judged_repo)
    actor = ctx.budget_token()
    write_control(Repo(ctx.client, ctx.config.ledger_repo), actor, ctx.random_hex() + "-6")
    sha = head_sha(ctx, judged)
    rows: list[str] = []
    okays = [
        _denied(
            "write contents in the judged repo",
            put_contents(judged, actor, f"live-probe-6-{ctx.random_hex()}.txt"),
            rows,
        ),
        _denied(
            "create a check run in the judged repo",
            post_check_run(judged, actor, sha, "cheeseless live probe"),
            rows,
        ),
    ]
    if all(okays):
        return passed("6", "the budget token cannot write contents or create a check run", rows)
    return failed("6", "the budget token reaches beyond the ledger repository", rows)


@guarded("7")
def check_07_verdict_token_scope(ctx: Ctx) -> CheckResult:
    judged = Repo(ctx.client, ctx.config.judged_repo)
    ledger = Repo(ctx.client, ctx.config.ledger_repo)
    actor = ctx.verdict_token()
    sha = head_sha(ctx, judged)
    control = post_check_run(judged, actor, sha, "cheeseless live probe")
    if control.status != 201:
        raise Precondition(f"the verdict token cannot post a check run at all: {control.meta()}")
    rows = [f"control, post a check run: {control.meta()}"]
    okays = [
        _denied(
            "write contents in the judged repo",
            put_contents(judged, actor, f"live-probe-7-{ctx.random_hex()}.txt"),
            rows,
        ),
        _denied(
            "write contents in the ledger repo",
            put_contents(ledger, actor, f"live-probe-7-{ctx.random_hex()}.txt"),
            rows,
        ),
    ]
    if all(okays):
        return passed("7", "the verdict token posts checks but cannot write contents", rows)
    return failed("7", "the verdict token can write contents", rows)


@guarded("8")
def check_08_same_repo_protection(ctx: Ctx) -> CheckResult:
    if not ctx.config.same_repo:
        return not_applicable("8", "no same-repository opt-out configured")
    repo = Repo(ctx.client, ctx.config.same_repo)
    app = ctx.minter.mint(ctx.config.same_repo)
    branch = repo.default_branch(app)
    suffix = ctx.random_hex() + "-8"
    write_control(repo, app, suffix)
    resp = put_contents(repo, app, f"live-probe-{suffix}.txt", branch)
    # An explicit Contents conflict is a known malformed write, even on a protected branch.
    if resp.status == 409 and resp.get("message") == "sha was not supplied":
        raise Precondition("Contents conflict is not protection evidence")

    def evidence() -> bool:
        protection = repo.call("GET", f"branches/{branch}/protection", ctx.config.admin_token)
        admins = protection.get("enforce_admins")
        return (
            protection.status == 200 and isinstance(admins, dict) and admins.get("enabled") is True
        )

    if protection_rejection(resp, evidence=evidence):
        return passed("8", "default-branch protection binds the App", [resp.meta()])
    return failed("8", "the App wrote to the protected default branch", [resp.meta()])
