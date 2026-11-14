"""Prepare the operator prerequisites of the live GitHub release gate (phase 13).

Every step is idempotent and a dry run unless ``--apply`` is given. GitHub is reached only
through the ``gh`` CLI, so this script never reads, stores or prints a token. Disposable
repositories are always private and always named ``cheeseless-canary-*``; teardown refuses
anything else.

Usage::

    uv run python scripts/live_gate_setup.py setup --owner OWNER --app-id ID [--apply]
    uv run python scripts/live_gate_setup.py manifest --owner OWNER --key-file PATH [--apply]
    uv run python scripts/live_gate_setup.py teardown --owner OWNER [--apply]

See ``docs/live-release-gate.md`` for the run order.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import secrets
import stat
import subprocess  # noqa: S404 - the gh CLI is the only GitHub boundary
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

PREFIX = "cheeseless-canary-"
ENVIRONMENT = "cheeseless-judge"
CHECK_NAME = "cheeseless judge"
BUDGET_REF = "refs/heads/cheeseless/budget/**"
JUDGE_FILE = ".github/workflows/live-canary-judge.yml"
CI_FILE = ".github/workflows/live-canary-ci.yml"
SECRET_NAME = "CHEESELESS_LIVE_CANARY_SECRET"  # noqa: S105 - a name, never a value
ROLES = ("ledger", "judged", "fresh")

CI_WORKFLOW = """name: live-canary-ci
on: [push, pull_request]
permissions: {}
jobs:
  noop:
    runs-on: ubuntu-latest
    steps:
      - run: "true"
"""

JUDGE_WORKFLOW = f"""name: live-canary-judge
on:
  workflow_run:
    workflows: ["live-canary-ci"]
    types: [completed]
permissions: {{}}
jobs:
  judge:
    runs-on: ubuntu-latest
    environment: {ENVIRONMENT}
    steps:
      - name: Read the environment secret
        env:
          CANARY: ${{{{ secrets.{SECRET_NAME} }}}}
        run: test -n "$CANARY"
"""


class SetupError(Exception):
    """A step cannot continue; the message is safe to print."""


class Api(Protocol):
    def call(self, method: str, path: str, body: object = None) -> tuple[int, Any]: ...


class SecretWriter(Protocol):
    def set(self, repo: str, name: str, value: str, *, environment: str) -> None: ...


class GhApi:
    """``gh api`` as a transport. Authentication stays inside gh."""

    def call(self, method: str, path: str, body: object = None) -> tuple[int, Any]:
        cmd = ["gh", "api", "-X", method, path.lstrip("/")]
        data = None
        if body is not None:
            cmd += ["--input", "-"]
            data = json.dumps(body)
        done = subprocess.run(  # noqa: S603
            cmd, input=data, capture_output=True, text=True, check=False, timeout=60
        )
        parsed: Any = None
        if done.stdout.strip():
            try:
                parsed = json.loads(done.stdout)
            except json.JSONDecodeError:
                parsed = None
        if done.returncode == 0:
            return 200, parsed
        found = re.search(r"HTTP (\d{3})", done.stderr)
        return (int(found.group(1)) if found else 599), parsed


class GhSecrets:
    def set(self, repo: str, name: str, value: str, *, environment: str) -> None:
        done = subprocess.run(  # noqa: S603
            ["gh", "secret", "set", name, "--repo", repo, "--env", environment],  # noqa: S607
            input=value,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if done.returncode != 0:
            raise SetupError(f"gh secret set failed for {name} (exit {done.returncode})")


@dataclass(frozen=True)
class Plan:
    owner: str
    suffix: str
    app_id: int | None
    org: bool = False
    same_repo: bool = False

    def repo(self, role: str) -> str:
        return f"{self.owner}/{PREFIX}{self.suffix}-{role}"

    @property
    def roles(self) -> tuple[str, ...]:
        return (*ROLES, "same") if self.same_repo else ROLES


Say = Callable[[str], None]


def _canary(full: str) -> str:
    owner, _, name = full.partition("/")
    if not owner or not name.startswith(PREFIX):
        raise SetupError(f"refusing {full!r}: not a {PREFIX}* repository")
    return full


def _ok(status: int) -> bool:
    return 200 <= status < 300


def _expect(status: int, what: str, allowed: Sequence[int] = ()) -> None:
    if not _ok(status) and status not in allowed:
        raise SetupError(f"{what} failed with HTTP {status}")


def _default_branch(api: Api, repo: str) -> str:
    status, body = api.call("GET", f"repos/{repo}")
    _expect(status, f"read {repo}")
    name = body.get("default_branch") if isinstance(body, dict) else None
    return name if isinstance(name, str) else "main"


def ensure_repos(api: Api, plan: Plan, apply: bool, say: Say) -> None:
    for role in plan.roles:
        full = _canary(plan.repo(role))
        status, _ = api.call("GET", f"repos/{full}")
        if _ok(status):
            say(f"repo {full}: exists")
            continue
        if status != 404:
            raise SetupError(f"read {full} failed with HTTP {status}")
        say(f"repo {full}: {'creating' if apply else 'would create'} (private, initialised)")
        if apply:
            path = f"orgs/{plan.owner}/repos" if plan.org else "user/repos"
            body = {"name": full.split("/")[1], "private": True, "auto_init": True}
            _expect(api.call("POST", path, body)[0], f"create {full}")


def ensure_environment(api: Api, plan: Plan, apply: bool, say: Say) -> None:
    repo = plan.repo("judged")
    say(f"environment {ENVIRONMENT} on {repo}: default-branch-only deployment rule")
    if not apply:
        return
    body = {
        "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}
    }
    _expect(api.call("PUT", f"repos/{repo}/environments/{ENVIRONMENT}", body)[0], "environment")
    base = f"repos/{repo}/environments/{ENVIRONMENT}/deployment-branch-policies"
    status, listing = api.call("GET", base)
    _expect(status, "list deployment policies")
    existing = listing.get("branch_policies", []) if isinstance(listing, dict) else []
    default = _default_branch(api, repo)
    for policy in existing:
        if policy.get("name") != default:
            _expect(api.call("DELETE", f"{base}/{policy.get('id')}")[0], "drop stray policy")
    if not any(p.get("name") == default for p in existing):
        _expect(api.call("POST", base, {"name": default, "type": "branch"})[0], "add policy")


def ensure_secret(secrets_port: SecretWriter, plan: Plan, apply: bool, say: Say) -> None:
    repo = plan.repo("judged")
    say(f"secret {SECRET_NAME} in environment {ENVIRONMENT}: random value, never printed")
    if apply:
        secrets_port.set(repo, SECRET_NAME, secrets.token_hex(16), environment=ENVIRONMENT)


def _put_file(api: Api, repo: str, path: str, text: str, branch: str) -> None:
    status, current = api.call("GET", f"repos/{repo}/contents/{path}?ref={branch}")
    body: dict[str, Any] = {
        "message": "chore: live canary workflow",
        "content": base64.b64encode(text.encode()).decode(),
        "branch": branch,
    }
    if _ok(status) and isinstance(current, dict):
        if base64.b64decode(str(current.get("content", ""))).decode() == text:
            return
        body["sha"] = current.get("sha")
    _expect(api.call("PUT", f"repos/{repo}/contents/{path}", body)[0], f"write {path}")


def ensure_workflows(api: Api, plan: Plan, apply: bool, say: Say) -> None:
    repo = plan.repo("judged")
    say(f"canary workflows on the default branch of {repo}")
    if apply:
        branch = _default_branch(api, repo)
        _put_file(api, repo, CI_FILE, CI_WORKFLOW, branch)
        _put_file(api, repo, JUDGE_FILE, JUDGE_WORKFLOW, branch)


def _rulesets(plan: Plan) -> dict[str, dict[str, Any]]:
    target = {"ref_name": {"include": [BUDGET_REF], "exclude": []}}
    app = [{"actor_id": plan.app_id, "actor_type": "Integration", "bypass_mode": "always"}]
    base: dict[str, Any] = {"target": "branch", "enforcement": "active", "conditions": target}
    return {
        "cheeseless-budget-A": {
            **base,
            "name": "cheeseless-budget-A",
            "bypass_actors": [],
            "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
        },
        "cheeseless-budget-B": {
            **base,
            "name": "cheeseless-budget-B",
            "bypass_actors": app,
            "rules": [{"type": "creation"}, {"type": "update"}],
        },
    }


def ensure_rulesets(api: Api, plan: Plan, apply: bool, say: Say) -> None:
    repo = plan.repo("ledger")
    if plan.app_id is None:
        raise SetupError("rulesets need --app-id (Ruleset B bypasses the judge App only)")
    status, listing = api.call("GET", f"repos/{repo}/rulesets")
    if status in {403, 404}:
        say(
            f"rulesets on {repo}: HTTP {status}; the plan may not support them (check 1 reports it)"
        )
        if apply:
            raise SetupError("rulesets are unavailable on this plan or the token lacks admin")
    existing = {r.get("name") for r in listing} if isinstance(listing, list) else set()
    for name, body in _rulesets(plan).items():
        if name in existing:
            say(f"ruleset {name} on {repo}: exists")
            continue
        say(f"ruleset {name} on {repo}: {'creating' if apply else 'would create'}")
        if apply:
            _expect(api.call("POST", f"repos/{repo}/rulesets", body)[0], f"create {name}")


def ensure_required_check(api: Api, plan: Plan, apply: bool, say: Say) -> None:
    if plan.app_id is None:
        raise SetupError("the required check is pinned to the App; pass --app-id")
    targets = [plan.repo("judged"), *([plan.repo("same")] if plan.same_repo else [])]
    for repo in targets:
        say(f"required check {CHECK_NAME!r} pinned to App {plan.app_id} on {repo}")
        if not apply:
            continue
        body = {
            "required_status_checks": {
                "strict": False,
                "checks": [{"context": CHECK_NAME, "app_id": plan.app_id}],
            },
            "enforce_admins": True,
            "required_pull_request_reviews": None,
            "restrictions": None,
        }
        branch = _default_branch(api, repo)
        _expect(api.call("PUT", f"repos/{repo}/branches/{branch}/protection", body)[0], "protect")


def run_setup(
    api: Api, secrets_port: SecretWriter, plan: Plan, *, apply: bool, say: Say
) -> list[str]:
    """Run every step in order; the result lists env var names the operator must export."""
    say(f"mode: {'APPLY' if apply else 'dry run (no changes; pass --apply)'}")
    ensure_repos(api, plan, apply, say)
    ensure_environment(api, plan, apply, say)
    ensure_secret(secrets_port, plan, apply, say)
    ensure_workflows(api, plan, apply, say)
    ensure_rulesets(api, plan, apply, say)
    ensure_required_check(api, plan, apply, say)
    names = [
        f"CHEESELESS_LIVE_LEDGER_REPO={plan.repo('ledger')}",
        f"CHEESELESS_LIVE_JUDGED_REPO={plan.repo('judged')}",
        f"CHEESELESS_LIVE_FRESH_REPO={plan.repo('fresh')}",
        f"CHEESELESS_LIVE_CANARY_SECRET={SECRET_NAME}",
    ]
    if plan.same_repo:
        names.append(f"CHEESELESS_LIVE_SAME_REPO={plan.repo('same')}")
    return names


def run_teardown(api: Api, plan: Plan, *, apply: bool, say: Say) -> None:
    for role in (*ROLES, "same"):
        full = _canary(plan.repo(role))
        status, _ = api.call("GET", f"repos/{full}")
        if not _ok(status):
            say(f"repo {full}: absent")
            continue
        say(f"repo {full}: {'deleting' if apply else 'would delete'}")
        if apply:
            _expect(api.call("DELETE", f"repos/{full}")[0], f"delete {full}")


# ----- GitHub App manifest flow ------------------------------------------------------------
def manifest(redirect: str) -> dict[str, Any]:
    return {
        "name": f"cheeseless-judge-canary-{secrets.token_hex(3)}",
        "url": "https://example.com",
        "redirect_url": redirect,
        "public": False,
        "default_permissions": {"checks": "write", "contents": "write"},
        "default_events": [],
    }


def write_key(path: Path, pem: str) -> None:
    """Store the private key at mode 0600; refuse an existing file and any path in a repo."""
    resolved = path.expanduser().resolve()
    for parent in (resolved.parent, *resolved.parents):
        if (parent / ".git").exists():
            raise SetupError("refusing to store the App key inside a git repository")
    fd = os.open(resolved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as handle:
        handle.write(pem)


def run_manifest(
    api: Api,
    owner: str,
    key_file: Path,
    *,
    org: bool,
    apply: bool,
    say: Say,
    wait: float = 600.0,
    port: int = 0,
) -> int | None:
    if not apply:
        say("manifest flow: would start a local callback server and print one URL (pass --apply)")
        return None
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}
    done = threading.Event()
    holder: dict[str, HTTPServer] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/start":
                target = f"organizations/{owner}/settings/apps/new" if org else "settings/apps/new"
                page = (
                    f'<form id="f" method="post" action="https://github.com/{target}'
                    f'?state={state}"><input type="hidden" name="manifest" '
                    f'value="{html.escape(json.dumps(manifest(redirect)))}"></form>'
                    '<script>document.getElementById("f").submit()</script>'
                )
                self._reply(200, page)
            elif url.path == "/callback" and query.get("state") == [state] and "code" in query:
                result["code"] = query["code"][0]
                self._reply(200, "App created. Return to the terminal; you can close this tab.")
                done.set()
            else:
                self._reply(400, "unexpected request")

        def _reply(self, code: int, text: str) -> None:
            data = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", port), Handler)
    holder["s"] = server
    redirect = f"http://127.0.0.1:{server.server_port}/callback"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    say(f"open this URL and click through GitHub once: http://127.0.0.1:{server.server_port}/start")
    try:
        if not done.wait(wait):
            raise SetupError("timed out waiting for the GitHub callback")
    finally:
        server.shutdown()
        server.server_close()
    status, body = api.call("POST", f"app-manifests/{result['code']}/conversions", {})
    _expect(status, "manifest conversion")
    if not isinstance(body, dict) or "pem" not in body or "id" not in body:
        raise SetupError("manifest conversion returned an unexpected body")
    write_key(key_file, str(body["pem"]))
    say(f"App id {body['id']} created; private key stored at {key_file} (mode 0600)")
    home = body.get("html_url", "")
    say(f"install it on the ledger and judged repositories: {home}/installations/new")
    return int(body["id"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "manifest", "teardown"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--owner", required=True)
        cmd.add_argument("--org", action="store_true", help="owner is an organisation")
        cmd.add_argument("--apply", action="store_true", help="perform the changes")
        if name != "manifest":
            cmd.add_argument("--suffix", default="gate")
            cmd.add_argument("--same-repo", action="store_true")
        if name == "setup":
            cmd.add_argument("--app-id", type=int)
        if name == "manifest":
            cmd.add_argument("--key-file", type=Path, required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    api: Api | None = None,
    secrets_port: SecretWriter | None = None,
    say: Say = print,
) -> int:
    args = build_parser().parse_args(argv)
    api = api or GhApi()
    try:
        if args.command == "manifest":
            run_manifest(api, args.owner, args.key_file, org=args.org, apply=args.apply, say=say)
            return 0
        plan = Plan(
            args.owner, args.suffix, getattr(args, "app_id", None), args.org, args.same_repo
        )
        if args.command == "teardown":
            run_teardown(api, plan, apply=args.apply, say=say)
            return 0
        names = run_setup(api, secrets_port or GhSecrets(), plan, apply=args.apply, say=say)
    except SetupError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    say("export these before running the suite (plus the token and App variables in the docs):")
    for line in names:
        say(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
