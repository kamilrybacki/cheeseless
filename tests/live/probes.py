"""Branch, workflow and run helpers for the workflow-isolation checks (9-11)."""

from __future__ import annotations

import base64
from collections.abc import Callable

from tests.live.github_client import Token
from tests.live.model import Ctx
from tests.live.ref_ops import Precondition, Repo, need, poll, sha_of

JUDGE_FILE = ".github/workflows/live-canary-judge.yml"
CI_FILE = ".github/workflows/live-canary-ci.yml"
CHECK_NAME = "cheeseless judge"
ENV = "cheeseless-judge"


def branch_workflow(secret: str, check_name: str) -> str:
    """A workflow a pusher adds on their own branch: it tries the secret and posts a check."""
    return f"""name: live-probe
on: push
permissions:
  checks: write
jobs:
  control:
    runs-on: ubuntu-latest
    steps:
      - run: "true"
  with-env:
    runs-on: ubuntu-latest
    environment: {ENV}
    steps:
      - name: secret-absent
        env:
          S: ${{{{ secrets.{secret} }}}}
        run: test -z "$S"
      - name: secret-present
        if: failure()
        env:
          S: ${{{{ secrets.{secret} }}}}
        run: test -n "$S"
  no-env:
    runs-on: ubuntu-latest
    steps:
      - name: secret-absent
        env:
          S: ${{{{ secrets.{secret} }}}}
        run: test -z "$S"
      - name: secret-present
        if: failure()
        env:
          S: ${{{{ secrets.{secret} }}}}
        run: test -n "$S"
  fake-check:
    runs-on: ubuntu-latest
    steps:
      - name: post
        env:
          GH_TOKEN: ${{{{ github.token }}}}
        run: >
          gh api repos/${{{{ github.repository }}}}/check-runs -X POST
          -f name='{check_name}' -f head_sha=${{{{ github.sha }}}}
          -f status=completed -f conclusion=success
"""


def put_file(repo: Repo, token: Token, path: str, text: str, branch: str) -> str:
    body = {
        "message": "cheeseless live probe",
        "content": base64.b64encode(text.encode()).decode(),
        "branch": branch,
    }
    resp = repo.call("PUT", f"contents/{path}", token, body)
    need(resp, {200, 201}, f"pusher cannot write {path} on {branch}")
    commit = resp.get("commit")
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str):
        raise Precondition("the write returned no commit")
    return sha


def pusher_branch(ctx: Ctx, repo: Repo, suffix: str) -> str:
    """Create ``live-probe/<hex>-<suffix>`` from the default branch with the pusher token."""
    pusher = ctx.config.pusher_token
    base = repo.get_ref(repo.default_branch(pusher), pusher)
    if base is None:
        raise Precondition("the judged repository has no default-branch commit")
    branch = f"live-probe/{ctx.random_hex()}-{suffix}"
    need(repo.create_ref(branch, base, pusher), {201}, "pusher cannot create a branch")
    return branch


def drop_branch(repo: Repo, token: Token, branch: str) -> None:
    repo.delete_ref(branch, token)


def runs_for(
    repo: Repo, token: Token, sha: str, workflow: str | None = None
) -> list[dict[str, object]]:
    path = f"actions/workflows/{workflow}/runs" if workflow else "actions/runs"
    resp = repo.call("GET", f"{path}?head_sha={sha}", token)
    if resp.status != 200:
        return []
    items = resp.get("workflow_runs")
    return [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []


def completed_run(
    ctx: Ctx, repo: Repo, token: Token, sha: str, workflow: str | None, name: str | None
) -> dict[str, object] | None:
    found: list[dict[str, object]] = []

    def probe() -> bool:
        runs = [
            r
            for r in runs_for(repo, token, sha, workflow)
            if r.get("status") == "completed" and (name is None or r.get("name") == name)
        ]
        found[:] = runs[:1]
        return bool(found)

    return found[0] if poll(probe, sleep=ctx.sleep, seconds=ctx.poll_seconds) else None


def job_conclusions(repo: Repo, token: Token, run: dict[str, object]) -> dict[str, str]:
    run_id = run.get("id")
    if not isinstance(run_id, int):
        raise Precondition("a workflow run has no id")
    resp = repo.call("GET", f"actions/runs/{run_id}/jobs", token)
    jobs = resp.get("jobs") if resp.status == 200 else None
    out: dict[str, str] = {}
    for job in jobs if isinstance(jobs, list) else []:
        if isinstance(job, dict):
            out[str(job.get("name"))] = str(job.get("conclusion"))
    return out


def tip_of(repo: Repo, token: Token, branch: str) -> str:
    resp = need(repo.call("GET", f"git/ref/heads/{branch}", token), {200}, "read the probe branch")
    return sha_of(resp, "object", "sha")


Probe = Callable[[], None]
