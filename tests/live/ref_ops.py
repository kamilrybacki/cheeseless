"""Git-data helpers shared by the live checks. Every failure here is a missing precondition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlencode

if TYPE_CHECKING:
    from tests.live.model import Ctx

from tests.live.github_client import GitHubClient, Response, Token

REJECT = frozenset({403, 422})
SCOPE_DENIED = frozenset({403, 404})


class Precondition(Exception):
    """A prerequisite is missing. The check reports it and never passes."""


def need(resp: Response, ok: set[int] | frozenset[int], what: str) -> Response:
    if resp.status not in ok:
        raise Precondition(f"{what}: {resp.meta()}")
    return resp


def sha_of(resp: Response, *path: str) -> str:
    node: object = resp.body
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    if not isinstance(node, str) or len(node) != 40:
        raise Precondition(f"response has no usable object id ({resp.meta()})")
    return node


@dataclass(frozen=True)
class Repo:
    client: GitHubClient
    full: str

    def call(self, method: str, path: str, token: Token, body: object = None) -> Response:
        # GitHub answers 404 for a trailing slash, so the repo root has no "/" suffix.
        url = f"repos/{self.full}/{path}" if path else f"repos/{self.full}"
        return self.client.request(method, url, token=token, json=body)

    def default_branch(self, token: Token) -> str:
        resp = need(self.call("GET", "", token), {200}, f"read {self.full}")
        name = resp.get("default_branch")
        if not isinstance(name, str):
            raise Precondition(f"{self.full} has no default branch")
        return name

    def get_ref(self, branch: str, token: Token) -> str | None:
        resp = self.call("GET", f"git/ref/heads/{branch}", token)
        if resp.status == 404:
            return None
        return sha_of(need(resp, {200}, f"read ref {branch}"), "object", "sha")

    def tree_of(self, sha: str, token: Token) -> str:
        resp = need(self.call("GET", f"git/commits/{sha}", token), {200}, f"read commit {sha[:7]}")
        return sha_of(resp, "tree", "sha")

    def write_commit(self, tree: str, parents: list[str], message: str, token: Token) -> str:
        resp = self.call(
            "POST", "git/commits", token, {"message": message, "tree": tree, "parents": parents}
        )
        return sha_of(need(resp, {201}, "write commit"), "sha")

    def child(self, tip: str, token: Token, message: str) -> str:
        return self.write_commit(self.tree_of(tip, token), [tip], message, token)

    def orphan(self, tip: str, token: Token, message: str) -> str:
        return self.write_commit(self.tree_of(tip, token), [], message, token)

    def create_ref(self, branch: str, sha: str, token: Token) -> Response:
        return self.call("POST", "git/refs", token, {"ref": f"refs/heads/{branch}", "sha": sha})

    def update_ref(self, branch: str, sha: str, token: Token, *, force: bool) -> Response:
        return self.call("PATCH", f"git/refs/heads/{branch}", token, {"sha": sha, "force": force})

    def delete_ref(self, branch: str, token: Token) -> Response:
        return self.call("DELETE", f"git/refs/heads/{branch}", token)


def poll(
    probe: Callable[[], bool], *, sleep: Callable[[float], None], seconds: float, every: float = 5.0
) -> bool:
    """Run ``probe`` until it is true or the budget is spent. ``sleep`` is injectable."""
    waited = 0.0
    while True:
        if probe():
            return True
        if waited >= seconds:
            return False
        sleep(every)
        waited += every


def protection_rejection(resp: Response, *, evidence: Callable[[], bool]) -> bool:
    if resp.status == 401:
        raise Precondition("credential authentication failed")
    if 200 <= resp.status < 300:
        return False
    if resp.status in {403, 409, 422} and evidence():
        return True
    raise Precondition("rejection has no attributable protection evidence")


def actor_name(ctx: Ctx, actor: str, token: Token) -> str:
    if actor != "pusher":
        return ctx.minter.actor_name()
    resp = need(ctx.client.request("GET", "user", token=token), {200}, "read pusher identity")
    login = resp.get("login")
    if not isinstance(login, str) or not login:
        raise Precondition("pusher identity is unavailable")
    return login


def attempt_started() -> datetime:
    # GitHub timestamps have second precision; run-unique refs isolate attempts in that second.
    return datetime.now(UTC).replace(microsecond=0)


def ruleset_rejection(
    ctx: Ctx, repo: Repo, resp: Response, *, ref: str, actor: str, started: datetime
) -> bool:
    def evidence() -> bool:
        query = urlencode({"ref": ref, "time_period": "day", "per_page": 100})
        suites = need(
            repo.call("GET", f"rulesets/rule-suites?{query}", ctx.config.admin_token),
            {200},
            "read rule suites with admin credential",
        )
        if not isinstance(suites.body, list):
            return False
        # The API returns newest first. Bypass and other actors/refs cannot prove rejection.
        for suite in suites.body:
            if not isinstance(suite, dict):
                continue
            if (suite.get("ref"), suite.get("result"), suite.get("actor_name")) != (
                ref,
                "fail",
                actor,
            ):
                continue
            try:
                pushed = datetime.fromisoformat(str(suite.get("pushed_at")))
                if pushed.tzinfo is not None and pushed >= started:
                    return True
            except ValueError:
                continue
        return False

    return protection_rejection(
        resp, evidence=lambda: poll(evidence, sleep=ctx.sleep, seconds=ctx.poll_seconds)
    )


def write_control(repo: Repo, actor: Token, suffix: str) -> None:
    from tests.live.checks_tokens import put_contents

    base = repo.get_ref(repo.default_branch(actor), actor)
    if base is None:
        raise Precondition("write control has no base commit")
    branch = f"live-control/{suffix}"
    need(repo.create_ref(branch, base, actor), {201}, "create unprotected control")
    try:
        need(put_contents(repo, actor, f"control-{suffix}.txt", branch), {201}, "write control")
    finally:
        need(repo.delete_ref(branch, actor), {204}, "clean up control")
