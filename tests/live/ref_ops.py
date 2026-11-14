"""Git-data helpers shared by the live checks. Every failure here is a missing precondition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tests.live.github_client import GitHubClient, Response, Token

REJECT = frozenset({401, 403, 422})
SCOPE_DENIED = frozenset({401, 403, 404})


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
        return self.client.request(method, f"repos/{self.full}/{path}", token=token, json=body)

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
