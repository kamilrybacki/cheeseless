"""A stateful in-memory GitHub for the default (no network) tests of the live checks.

Actors are told apart by ``Token.label``: ``pusher``, ``admin``, ``app``, ``budget``,
``verdict`` and ``fresh``. Knobs on :class:`Knobs` break one protection at a time so a test can
prove that the matching check turns red instead of passing silently.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from tests.live.config import LiveConfig
from tests.live.github_client import ClientError, Response, Token
from tests.live.seeding_port import SeedOutcome

LEDGER = "octo/cheeseless-canary-ledger"
JUDGED = "octo/cheeseless-canary-judged"
FRESH = "octo/cheeseless-canary-fresh"
SAME = "octo/cheeseless-canary-same"
APP_ID = "4242"
CHECK = "cheeseless judge"
_BUDGET = "cheeseless/budget/"


@dataclass
class Knobs:
    rulesets_enforced: bool = True  # False: A and B do nothing
    lost_update: bool = False  # True: non-fast-forward updates are not refused
    pusher_access: bool = True  # False: the ledger answers the pusher with 404
    plan_blocks_rulesets: bool = False
    ruleset_b_present: bool = True
    ruleset_a_present: bool = True
    a_has_bypass: bool = False
    b_bypass_app: bool = True
    budget_reaches_judged: bool = False  # budget token can write outside the ledger
    verdict_writes_contents: bool = False
    verdict_can_post: bool = True
    same_repo_protected: bool = True
    branch_reads_secret: bool = False
    actions_enabled: bool = True
    canary_workflows: bool = True
    judge_succeeds: bool = True
    pin_to_app: bool = True
    actions_can_forge: bool = False
    seed_ok: bool = True
    unreadable_orphan: bool = False
    stale_reads: bool = False


@dataclass
class RepoState:
    full: str
    repo_id: int
    default: str = "main"
    commits: dict[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)
    runs: list[dict[str, object]] = field(default_factory=list)
    jobs: dict[int, dict[str, str]] = field(default_factory=dict)
    pulls: dict[int, dict[str, object]] = field(default_factory=dict)
    files: set[tuple[str, str]] = field(default_factory=set)
    checks: list[tuple[str, str]] = field(default_factory=list)  # (sha, source label)


class FakeGitHub:
    """Implements the ``GitHubClient`` protocol."""

    def __init__(self, knobs: Knobs | None = None) -> None:
        self.knobs = knobs or Knobs()
        self.lock = threading.Lock()
        self.log: list[tuple[str, str, str, int]] = []
        self._n = 0
        self.repos = {
            full: self._fresh_repo(full, i)
            for i, full in enumerate((LEDGER, JUDGED, FRESH, SAME), 1)
        }

    def _sha(self) -> str:
        self._n += 1
        return hashlib.sha1(f"fake-{self._n}".encode()).hexdigest()  # noqa: S324

    def _fresh_repo(self, full: str, repo_id: int) -> RepoState:
        repo = RepoState(full, 1000 + repo_id)
        root = self._sha()
        repo.commits[root] = ("tree-0", ())
        repo.refs["main"] = root
        return repo

    # ----- GitHubClient -------------------------------------------------------------------
    def request(
        self, method: str, path: str, *, token: Token | None = None, json: object = None
    ) -> Response:
        label = token.label if token else "anonymous"
        with self.lock:
            resp = self._route(method, path.lstrip("/"), label, json)
        self.log.append((method, path, label, resp.status))
        return resp

    def _route(self, method: str, path: str, label: str, body: object) -> Response:
        if path == "installation/token" and method == "DELETE":
            return Response(204)
        if path.endswith("/"):  # live GitHub answers 404 for a trailing slash (found live)
            return Response(404, {"message": "Not Found"})
        found = re.fullmatch(r"repos/([^/]+/[^/]+)(?:/(.*))?", path)
        if not found or found.group(1) not in self.repos:
            return Response(404, {"message": "Not Found"})
        repo = self.repos[found.group(1)]
        rest, _, query = (found.group(2) or "").partition("?")
        if repo.full == LEDGER and label == "pusher" and not self.knobs.pusher_access:
            return Response(404, {"message": "Not Found"})
        data = body if isinstance(body, dict) else {}
        for pattern, handler in self._routes():
            m = re.fullmatch(pattern[1], rest)
            if m and pattern[0] == method:
                return handler(repo, label, data, query, *m.groups())
        return Response(404, {"message": "Not Found"})

    def _routes(self):  # type: ignore[no-untyped-def]
        return [
            (("GET", ""), self._get_repo),
            (("GET", r"git/ref/heads/(.+)"), self._get_ref),
            (("GET", r"git/commits/([0-9a-f]{40})"), self._get_commit),
            (("POST", "git/commits"), self._post_commit),
            (("POST", "git/refs"), self._post_ref),
            (("PATCH", r"git/refs/heads/(.+)"), self._patch_ref),
            (("DELETE", r"git/refs/heads/(.+)"), self._delete_ref),
            (("GET", "rulesets"), self._list_rulesets),
            (("GET", r"rulesets/(\d+)"), self._get_ruleset),
            (("PUT", r"contents/(.+)"), self._put_contents),
            (("GET", r"contents/(.+)"), self._get_contents),
            (("POST", "check-runs"), self._post_check),
            (("GET", r"commits/([^/]+)/check-runs"), self._list_checks),
            (("GET", r"actions/workflows/([^/]+)/runs"), self._list_runs),
            (("GET", "actions/runs"), self._list_runs),
            (("GET", r"actions/runs/(\d+)/jobs"), self._run_jobs),
            (("GET", r"branches/([^/]+)/protection/required_status_checks"), self._required),
            (("POST", "pulls"), self._open_pull),
            (("GET", r"pulls/(\d+)"), self._get_pull),
            (("PATCH", r"pulls/(\d+)"), self._close_pull),
        ]

    # ----- repository and refs ------------------------------------------------------------
    def _get_repo(self, repo: RepoState, label: str, *_: object) -> Response:
        push = not (repo.full == LEDGER and label == "pusher" and not self.knobs.pusher_access)
        return Response(
            200,
            {"default_branch": repo.default, "id": repo.repo_id, "permissions": {"push": push}},
        )

    def _get_ref(
        self, repo: RepoState, label: str, data: object, query: str, branch: str
    ) -> Response:
        sha = repo.refs.get(branch)
        if sha is None:
            return Response(404, {"message": "Not Found"})
        if self.knobs.stale_reads and label == "pusher" and repo.full == LEDGER:
            sha = repo.commits[sha][1][0] if repo.commits[sha][1] else sha
        return Response(200, {"object": {"sha": sha}})

    def _get_commit(
        self, repo: RepoState, label: str, data: object, query: str, sha: str
    ) -> Response:
        if sha not in repo.commits or (
            self.knobs.unreadable_orphan
            and not repo.commits[sha][1]
            and sha != repo.refs.get("main")
        ):
            return Response(404, {"message": "Not Found"})
        tree, parents = repo.commits[sha]
        return Response(200, {"sha": sha, "tree": {"sha": hashlib.sha1(tree.encode()).hexdigest()}})  # noqa: S324

    def _post_commit(
        self, repo: RepoState, label: str, data: dict[str, object], query: str
    ) -> Response:
        sha = self._sha()
        parents = tuple(
            str(p) for p in data.get("parents", []) if isinstance(data.get("parents"), list)
        )  # type: ignore[union-attr]
        repo.commits[sha] = (str(data.get("tree")), parents)
        return Response(201, {"sha": sha})

    def _is_ledger(self, repo: RepoState, branch: str) -> bool:
        return repo.full == LEDGER and branch.startswith(_BUDGET)

    def _bypass(self, label: str) -> bool:
        return label in {"app", "budget", "verdict"} and self.knobs.b_bypass_app

    def _post_ref(
        self, repo: RepoState, label: str, data: dict[str, object], query: str
    ) -> Response:
        branch = str(data.get("ref", "")).removeprefix("refs/heads/")
        if label == "verdict":
            return Response(403, {"message": "Resource not accessible by integration"})
        if (
            self._is_ledger(repo, branch)
            and self.knobs.rulesets_enforced
            and not self._bypass(label)
        ):
            return Response(
                403, {"message": "Repository rule violations found: creations restricted"}
            )
        if branch in repo.refs:
            return Response(422, {"message": "Reference already exists"})
        repo.refs[branch] = str(data.get("sha"))
        return Response(201, {"ref": f"refs/heads/{branch}"})

    def _descends(self, repo: RepoState, new: str, old: str) -> bool:
        stack, seen = [new], set()
        while stack:
            cur = stack.pop()
            if cur == old:
                return True
            if cur in seen or cur not in repo.commits:
                continue
            seen.add(cur)
            stack.extend(repo.commits[cur][1])
        return False

    def _patch_ref(
        self, repo: RepoState, label: str, data: dict[str, object], query: str, branch: str
    ) -> Response:
        sha, force = str(data.get("sha")), data.get("force") is True
        if branch not in repo.refs:
            return Response(404, {"message": "Not Found"})
        if self._is_ledger(repo, branch) and self.knobs.rulesets_enforced:
            if not self._bypass(label):
                return Response(
                    403, {"message": "Repository rule violations found: update restricted"}
                )
            if force and not self._descends(repo, sha, repo.refs[branch]):
                return Response(
                    403, {"message": "Repository rule violations found: non_fast_forward"}
                )
            if force and self._descends(repo, repo.refs[branch], sha):
                return Response(
                    403, {"message": "Repository rule violations found: non_fast_forward"}
                )
        if (
            not force
            and not self.knobs.lost_update
            and not self._descends(repo, sha, repo.refs[branch])
        ):
            return Response(422, {"message": "Update is not a fast forward"})
        repo.refs[branch] = sha
        return Response(200, {"object": {"sha": sha}})

    def _delete_ref(
        self, repo: RepoState, label: str, data: object, query: str, branch: str
    ) -> Response:
        if self._is_ledger(repo, branch) and self.knobs.rulesets_enforced:
            return Response(403, {"message": "Repository rule violations found: deletion"})
        if label == "verdict" or branch not in repo.refs:
            return Response(404, {"message": "Not Found"})
        del repo.refs[branch]
        return Response(204)

    # ----- rulesets and protection --------------------------------------------------------
    def _list_rulesets(self, repo: RepoState, label: str, *_: object) -> Response:
        if self.knobs.plan_blocks_rulesets:
            return Response(
                403, {"message": "Upgrade to GitHub Pro or make this repository public"}
            )
        if label != "admin":
            return Response(403, {"message": "Must have admin rights"})
        items = []
        if self.knobs.ruleset_a_present:
            items.append({"id": 1})
        if self.knobs.ruleset_b_present:
            items.append({"id": 2})
        return Response(200, items)

    def _get_ruleset(
        self, repo: RepoState, label: str, data: object, query: str, rid: str
    ) -> Response:
        is_a = rid == "1"
        bypass: list[dict[str, object]] = []
        if is_a and self.knobs.a_has_bypass or (not is_a and self.knobs.b_bypass_app):
            bypass.append(
                {"actor_id": int(APP_ID), "actor_type": "Integration", "bypass_mode": "always"}
            )
        rules = ["deletion", "non_fast_forward"] if is_a else ["creation", "update"]
        return Response(
            200,
            {
                "name": "A" if is_a else "B",
                "enforcement": "active",
                "conditions": {"ref_name": {"include": ["refs/heads/cheeseless/budget/**"]}},
                "rules": [{"type": r} for r in rules],
                "bypass_actors": bypass,
            },
        )

    def _required(
        self, repo: RepoState, label: str, data: object, query: str, branch: str
    ) -> Response:
        app = int(APP_ID) if self.knobs.pin_to_app else 15368
        return Response(200, {"checks": [{"context": CHECK, "app_id": app}]})

    # ----- contents, checks, actions ------------------------------------------------------
    def _put_contents(
        self, repo: RepoState, label: str, data: dict[str, object], query: str, path: str
    ) -> Response:
        branch = str(data.get("branch") or repo.default)
        outside = repo.full != LEDGER
        if label == "budget" and outside and not self.knobs.budget_reaches_judged:
            return Response(404, {"message": "Not Found"})
        if label == "verdict" and not self.knobs.verdict_writes_contents:
            return Response(403, {"message": "Resource not accessible by integration"})
        if (branch, path) in repo.files and "sha" not in data:
            return Response(409, {"message": "sha was not supplied"})
        if repo.full == SAME and branch == repo.default and self.knobs.same_repo_protected:
            return Response(409, {"message": "Repository rule violations found"})
        if branch not in repo.refs:
            return Response(404, {"message": "Branch not found"})
        sha = self._sha()
        repo.commits[sha] = ("tree-" + sha, (repo.refs[branch],))
        repo.refs[branch] = sha
        repo.files.add((branch, path))
        self._after_push(repo, branch, path, sha)
        return Response(201, {"commit": {"sha": sha}})

    def _after_push(self, repo: RepoState, branch: str, path: str, sha: str) -> None:
        if repo.full != JUDGED or not self.knobs.actions_enabled or branch == repo.default:
            return
        if path.endswith("live-probe.yml"):
            read = "failure" if self.knobs.branch_reads_secret else "success"
            self._run(
                repo,
                sha,
                "live-probe.yml",
                "live-probe",
                "success",
                {"control": "success", "with-env": read, "no-env": read, "fake-check": "success"},
            )
            repo.checks.append((sha, "actions"))
        else:
            # Live: the push runs the CI workflow; its completion starts the workflow_run judge,
            # whose run carries the DEFAULT branch tip, never the probe commit (found live).
            ok = "success" if self.knobs.judge_succeeds else "failure"
            self._run(
                repo, sha, "live-canary-ci.yml", "live-canary-ci", "success", {"noop": "success"}
            )
            tip = repo.refs[repo.default]
            self._run(
                repo,
                tip,
                "live-canary-judge.yml",
                "live-canary-judge",
                ok,
                {"judge": ok},
                event="workflow_run",
            )

    def _run(
        self,
        repo: RepoState,
        sha: str,
        wf: str,
        name: str,
        concl: str,
        jobs: dict[str, str],
        event: str = "push",
    ) -> None:
        rid = len(repo.runs) + 1
        repo.runs.append(
            {
                "id": rid,
                "head_sha": sha,
                "workflow": wf,
                "name": name,
                "event": event,
                "status": "completed",
                "conclusion": concl,
            }
        )
        repo.jobs[rid] = jobs

    def _get_contents(
        self, repo: RepoState, label: str, data: object, query: str, path: str
    ) -> Response:
        if path.startswith(".github/workflows/") and not self.knobs.canary_workflows:
            return Response(404, {"message": "Not Found"})
        return Response(200, {"path": path})

    def _post_check(
        self, repo: RepoState, label: str, data: dict[str, object], query: str
    ) -> Response:
        if label == "budget" and not self.knobs.budget_reaches_judged:
            return Response(403, {"message": "Resource not accessible by integration"})
        if label == "verdict" and not self.knobs.verdict_can_post:
            return Response(403, {"message": "Resource not accessible by integration"})
        if data.get("name") == CHECK:
            repo.checks.append((str(data.get("head_sha")), label))
        return Response(201, {"id": len(repo.checks)})

    def _list_runs(
        self, repo: RepoState, label: str, data: object, query: str, wf: str = ""
    ) -> Response:
        params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
        runs = [
            r
            for r in repo.runs
            if (not wf or r["workflow"] == wf)
            and ("head_sha" not in params or r["head_sha"] == params["head_sha"])
            and ("event" not in params or r["event"] == params["event"])
        ]
        return Response(200, {"workflow_runs": runs})

    def _run_jobs(
        self, repo: RepoState, label: str, data: object, query: str, rid: str
    ) -> Response:
        jobs = repo.jobs.get(int(rid), {})
        return Response(
            200,
            {
                "jobs": [
                    {
                        "name": k,
                        "conclusion": v,
                        "steps": [
                            {"name": "secret-absent", "conclusion": v},
                            {
                                "name": "secret-present",
                                "conclusion": "success" if v == "failure" else "skipped",
                            },
                        ],
                    }
                    for k, v in jobs.items()
                ]
            },
        )

    def _list_checks(
        self, repo: RepoState, label: str, data: object, query: str, sha: str
    ) -> Response:
        return Response(
            200,
            {
                "check_runs": [
                    {
                        "head_sha": s,
                        "name": CHECK,
                        "conclusion": "success",
                        "app": {
                            "id": 15368 if source == "actions" else int(APP_ID),
                            "slug": "github-actions" if source == "actions" else "judge",
                        },
                    }
                    for s, source in repo.checks
                    if s == sha
                ]
            },
        )

    # ----- pull requests ------------------------------------------------------------------
    def _open_pull(
        self, repo: RepoState, label: str, data: dict[str, object], query: str
    ) -> Response:
        number = len(repo.pulls) + 1
        repo.pulls[number] = {"head": str(data.get("head")), "state": "open"}
        return Response(201, {"number": number})

    def _get_pull(
        self, repo: RepoState, label: str, data: object, query: str, number: str
    ) -> Response:
        pull = repo.pulls.get(int(number))
        if pull is None:
            return Response(404, {"message": "Not Found"})
        sha = repo.refs.get(str(pull["head"]), "")
        sources = {src for s, src in repo.checks if s == sha}
        satisfied = (
            "verdict" in sources
            or "app" in sources
            or ("actions" in sources and self.knobs.actions_can_forge)
        )
        return Response(200, {"mergeable_state": "clean" if satisfied else "blocked"})

    def _close_pull(
        self, repo: RepoState, label: str, data: dict[str, object], query: str, number: str
    ) -> Response:
        repo.pulls[int(number)]["state"] = "closed"
        return Response(200, {})


class FakeMinter:
    """Mints labelled tokens; the label decides what the fake lets the token do."""

    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def mint(
        self,
        repo: str,
        *,
        permissions: Mapping[str, str] | None = None,
        repositories: Sequence[str] | None = None,
    ) -> Token:
        if self.unavailable:
            raise ClientError("the App is not installed here")
        perms = dict(permissions) if permissions else None
        self.calls.append((repo, perms))
        label = "app"
        if perms == {"contents": "write"} and repo == LEDGER:
            label = "budget"
        elif perms == {"checks": "write"}:
            label = "verdict"
        elif repo == FRESH:
            label = "fresh"
        return Token(f"secret-value-{label}", label)


@dataclass
class FakeSeeder:
    ok: bool = True
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    def seed(self, repo: str, token: Token, *, repo_id: str, lineage: str) -> SeedOutcome:
        self.calls.append((repo, repo_id, lineage))
        if not self.ok:
            return SeedOutcome(False, "seed refused: nope")
        return SeedOutcome(True, "seeded", "a" * 40)


def make_config(*, same: bool = False) -> LiveConfig:
    from pathlib import Path

    return LiveConfig(
        app_id=APP_ID,
        key_file=Path("/nonexistent/key.pem"),
        pusher_token=Token("secret-value-pusher", "pusher"),
        admin_token=Token("secret-value-admin", "admin"),
        ledger_repo=LEDGER,
        judged_repo=JUDGED,
        fresh_repo=FRESH,
        canary_secret="CANARY_SECRET",  # noqa: S106
        installations={},
        same_repo=SAME if same else "",
    )
