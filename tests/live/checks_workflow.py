"""Release-gate checks 9-11: workflow isolation. Needs real Actions runs."""

from __future__ import annotations

from tests.live.model import CheckResult, Ctx, failed, guarded, passed
from tests.live.probes import (
    CHECK_NAME,
    CI_FILE,
    JUDGE_FILE,
    branch_workflow,
    completed_run,
    drop_branch,
    job_conclusions,
    pusher_branch,
    put_file,
)
from tests.live.ref_ops import Precondition, Repo, need


def _probe_branch(ctx: Ctx, repo: Repo, suffix: str) -> tuple[str, str]:
    """A pusher branch carrying the probe workflow: (branch, head sha)."""
    branch = pusher_branch(ctx, repo, suffix)
    text = branch_workflow(ctx.config.canary_secret, CHECK_NAME)
    sha = put_file(repo, ctx.config.pusher_token, ".github/workflows/live-probe.yml", text, branch)
    return branch, sha


@guarded("9")
def check_09_branch_workflow_blind(ctx: Ctx) -> CheckResult:
    repo = Repo(ctx.client, ctx.config.judged_repo)
    pusher = ctx.config.pusher_token
    branch, sha = _probe_branch(ctx, repo, "9")
    try:
        run = completed_run(ctx, repo, pusher, sha, "live-probe.yml", "live-probe")
        if run is None:
            raise Precondition("the branch workflow never completed (are Actions enabled?)")
        jobs = job_conclusions(repo, pusher, run)
    finally:
        drop_branch(repo, pusher, branch)
    rows = [f"job {name}: {state}" for name, state in sorted(jobs.items())]
    reads = [jobs.get("with-env"), jobs.get("no-env")]
    if "with-env" not in jobs or "no-env" not in jobs:
        raise Precondition(f"the probe run has no with-env/no-env jobs ({sorted(jobs)})")
    if "success" in reads:
        return failed("9", "a branch workflow read the canary environment secret", rows)
    return passed("9", "a pusher's branch workflow cannot read the environment secret", rows)


@guarded("10")
def check_10_judge_reads_secret(ctx: Ctx) -> CheckResult:
    repo = Repo(ctx.client, ctx.config.judged_repo)
    pusher = ctx.config.pusher_token
    app = ctx.minter.mint(ctx.config.judged_repo)
    default = repo.default_branch(app)
    for path in (JUDGE_FILE, CI_FILE):
        need(
            repo.call("GET", f"contents/{path}?ref={default}", app),
            {200},
            f"{path} is not installed on the default branch (run scripts/live_gate_setup.py)",
        )
    branch = pusher_branch(ctx, repo, "10")
    try:
        sha = put_file(repo, pusher, "live-probe-10.txt", "trigger\n", branch)
        run = completed_run(ctx, repo, pusher, sha, "live-canary-judge.yml", None)
    finally:
        drop_branch(repo, pusher, branch)
    if run is None:
        raise Precondition("the workflow_run judge never completed for the probe commit")
    row = f"workflow_run judge conclusion: {run.get('conclusion')}"
    if run.get("conclusion") == "success":
        return passed("10", "the default-branch judge reads the environment secret", [row])
    return failed("10", "the genuine judge could not read the environment secret", [row])


def _pin(ctx: Ctx, repo: Repo, default: str) -> list[str]:
    resp = need(
        repo.call(
            "GET", f"branches/{default}/protection/required_status_checks", ctx.config.admin_token
        ),
        {200},
        "no required status checks on the default branch",
    )
    checks = resp.get("checks")
    pinned = [
        f"{c.get('context')} app_id={c.get('app_id')}"
        for c in (checks if isinstance(checks, list) else [])
        if isinstance(c, dict) and c.get("context") == CHECK_NAME
    ]
    if not any(f"app_id={ctx.config.app_id}" in p for p in pinned):
        raise Precondition(f"{CHECK_NAME!r} is not required and pinned to the judge App")
    return pinned


def _state(repo: Repo, token_branch_pr: int, ctx: Ctx) -> str:
    resp = repo.call("GET", f"pulls/{token_branch_pr}", ctx.config.pusher_token)
    return str(resp.get("mergeable_state"))


@guarded("11")
def check_11_required_check_source(ctx: Ctx) -> CheckResult:
    from tests.live.checks_tokens import post_check_run
    from tests.live.ref_ops import poll

    repo = Repo(ctx.client, ctx.config.judged_repo)
    pusher = ctx.config.pusher_token
    default = repo.default_branch(ctx.minter.mint(ctx.config.judged_repo))
    rows = [f"required check pinned: {p}" for p in _pin(ctx, repo, default)]
    branch, sha = _probe_branch(ctx, repo, "11")
    number = 0
    try:
        pr = repo.call(
            "POST", "pulls", pusher, {"title": "live probe", "head": branch, "base": default}
        )
        made = need(pr, {201}, "pusher cannot open a pull request").get("number")
        number = made if isinstance(made, int) else 0
        run = completed_run(ctx, repo, pusher, sha, "live-probe.yml", "live-probe")
        if run is None:
            raise Precondition("the branch workflow never completed")
        fake = poll(
            lambda: _state(repo, number, ctx) in {"blocked", "clean", "unstable"},
            sleep=ctx.sleep,
            seconds=ctx.poll_seconds,
        )
        before = _state(repo, number, ctx)
        rows.append(f"after the github-actions check run: mergeable_state={before}")
        if not fake or before not in {"blocked"}:
            return failed("11", "a github-actions check run satisfied the required check", rows)
        post = post_check_run(repo, ctx.verdict_token(), sha, CHECK_NAME)
        rows.append(f"judge App check run: {post.meta()}")
        need(post, {201}, "the judge App could not post the check run")
        satisfied = poll(
            lambda: _state(repo, number, ctx) == "clean", sleep=ctx.sleep, seconds=ctx.poll_seconds
        )
    finally:
        if number:
            repo.call("PATCH", f"pulls/{number}", pusher, {"state": "closed"})
        drop_branch(repo, pusher, branch)
    if satisfied:
        return passed("11", "only the judge App's check run satisfies the required check", rows)
    return failed("11", "the judge App's check run did not satisfy the required check", rows)
