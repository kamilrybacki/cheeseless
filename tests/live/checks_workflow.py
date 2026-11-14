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
    triggered_judge_run,
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
    observer = ctx.config.admin_token
    branch, sha = _probe_branch(ctx, repo, "9")
    try:
        run = completed_run(ctx, repo, observer, sha, "live-probe.yml", "live-probe")
        if run is None:
            raise Precondition("the branch workflow never completed (are Actions enabled?)")
        jobs = job_conclusions(repo, observer, run)
    finally:
        drop_branch(repo, pusher, branch)
    rows = [f"job {name}: {state}" for name, state in sorted(jobs.items())]
    if jobs.get("control") != "success":
        raise Precondition("workflow execution control did not succeed")
    details = need(repo.call("GET", f"actions/runs/{run['id']}/jobs", observer), {200}, "jobs")
    items = details.get("jobs")
    probes = (
        {j.get("name"): j for j in items if isinstance(j, dict)} if isinstance(items, list) else {}
    )
    for name in ("with-env", "no-env"):
        job = probes.get(name, {})
        steps = {s.get("name"): s.get("conclusion") for s in job.get("steps", [])}
        if name == "with-env" and job.get("conclusion") == "failure" and not steps:
            # Live: the environment gate refuses a branch run before any step starts, so the
            # secret was never reachable. A job that ran steps is judged by them as before.
            rows.append("job with-env: blocked by the environment gate before any step ran")
            continue
        if steps.get("secret-present") == "success":
            return failed("9", "a branch workflow read the canary environment secret", rows)
        if job.get("conclusion") != "success" or steps.get("secret-absent") != "success":
            raise Precondition("secret absence was not demonstrated by a successful probe")
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
        run = triggered_judge_run(
            ctx, repo, ctx.config.admin_token, sha, "live-canary-ci.yml", "live-canary-judge.yml"
        )
    finally:
        drop_branch(repo, pusher, branch)
    if run is None:
        raise Precondition("the workflow_run judge never completed after the probe commit's CI run")
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
        if isinstance(c, dict)
        and c.get("context") == CHECK_NAME
        and c.get("app_id") == int(ctx.config.app_id)
    ]
    if not pinned:
        raise Precondition(f"{CHECK_NAME!r} is not required and pinned to the judge App")
    return pinned


def _state(repo: Repo, token_branch_pr: int, ctx: Ctx) -> str:
    resp = repo.call("GET", f"pulls/{token_branch_pr}", ctx.config.admin_token)
    return str(resp.get("mergeable_state"))


@guarded("11")
def check_11_required_check_source(ctx: Ctx) -> CheckResult:
    from tests.live.checks_tokens import post_check_run
    from tests.live.ref_ops import poll

    repo = Repo(ctx.client, ctx.config.judged_repo)
    pusher = ctx.config.pusher_token
    observer = ctx.config.admin_token
    default = repo.default_branch(ctx.minter.mint(ctx.config.judged_repo))
    rows = [f"required check pinned: {p}" for p in _pin(ctx, repo, default)]
    branch, sha = _probe_branch(ctx, repo, "11")
    number = 0
    try:
        # The pull request is scaffolding for the mergeable_state read; the pusher PAT has no
        # pull request scope (found live), so the admin token opens and closes it.
        pr = repo.call(
            "POST", "pulls", observer, {"title": "live probe", "head": branch, "base": default}
        )
        made = need(pr, {201}, "cannot open the probe pull request").get("number")
        number = made if isinstance(made, int) else 0
        run = completed_run(ctx, repo, observer, sha, "live-probe.yml", "live-probe")
        if run is None:
            raise Precondition("the branch workflow never completed")
        jobs = job_conclusions(repo, observer, run)
        if jobs.get("fake-check") != "success":
            raise Precondition("the forged-check job did not succeed")
        checks = need(repo.call("GET", f"commits/{sha}/check-runs", observer), {200}, "check runs")
        items = checks.get("check_runs")
        if not isinstance(items, list) or not any(
            isinstance(c, dict)
            and c.get("head_sha") == sha
            and c.get("name") == CHECK_NAME
            and c.get("conclusion") == "success"
            and isinstance(c.get("app"), dict)
            and c["app"].get("slug") == "github-actions"
            and c["app"].get("id") != int(ctx.config.app_id)
            for c in items
        ):
            raise Precondition("no successful GitHub Actions forged check exists on the target SHA")
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
            repo.call("PATCH", f"pulls/{number}", observer, {"state": "closed"})
        drop_branch(repo, pusher, branch)
    if satisfied:
        return passed("11", "only the judge App's check run satisfies the required check", rows)
    return failed("11", "the judge App's check run did not satisfy the required check", rows)
