"""Release-gate checks 1-5 and 12: the counter store (section 6 of the research note)."""

from __future__ import annotations

from tests.live.github_client import Response, Token
from tests.live.model import (
    CheckResult,
    Ctx,
    failed,
    guarded,
    passed,
    precondition_missing,
)
from tests.live.ref_ops import (
    REJECT,
    Precondition,
    Repo,
    actor_name,
    attempt_started,
    need,
    ruleset_rejection,
    sha_of,
    write_control,
)
from tests.live.rulesets import list_response, load_rulesets, require_b, ruleset_a, ruleset_b

ACTORS = ("pusher", "budget", "app")
OPS = ("creation", "fast_forward", "force_update", "rewind", "deletion")
WRITERS = 10
ROUNDS = 3


def expectation(actor: str, op: str) -> tuple[frozenset[int], str]:
    """Exact statuses a cell may show.

    Budget tokens and the judge App are one bypass actor: Ruleset B lets them create and
    append, Ruleset A (no bypass) still stops their delete, force update and rewind.
    """
    if actor != "pusher":
        if op == "creation":
            return frozenset({201}), "accepted (201)"
        if op == "fast_forward":
            return frozenset({200}), "accepted (200)"
    return REJECT, "rejected with ruleset evidence (403/422)"


def _tokens(ctx: Ctx) -> dict[str, Token]:
    return {
        "pusher": ctx.config.pusher_token,
        "budget": ctx.budget_token(),
        "app": ctx.app_token(),
    }


def _require_pusher_write(ctx: Ctx, repo: Repo) -> None:
    resp = need(
        repo.call("GET", "", ctx.config.pusher_token), {200}, "pusher cannot see the ledger"
    )
    perms = resp.get("permissions")
    if not (isinstance(perms, dict) and perms.get("push") is True):
        raise Precondition(
            "the pusher token needs write access to the ledger repository; without it GitHub "
            "answers 404 and the rulesets are never exercised"
        )


def _probe(ctx: Ctx, repo: Repo, name: str) -> tuple[str, str]:
    """A scratch ledger branch made by the App (Ruleset B lets it through): (branch, base sha)."""
    app = ctx.app_token()
    base = repo.get_ref(repo.default_branch(app), app)
    if base is None:
        raise Precondition("the ledger repository has no commit on its default branch")
    branch = f"cheeseless/budget/live-{ctx.random_hex()}-{name}"
    need(repo.create_ref(branch, base, app), {201}, "the App cannot create a scratch branch")
    return branch, base


@guarded("1")
def check_01_rulesets_available(ctx: Ctx) -> CheckResult:
    resp = list_response(ctx)
    message = str(resp.get("message") or "").lower()
    if resp.status in (403, 404) and "upgrade" in message:
        return failed(
            "1",
            "the ledger repository's plan does not offer rulesets on private repositories; "
            "use a plan that does, or keep CI unbudgeted and blocked",
            [resp.meta()],
        )
    if resp.status != 200:
        return precondition_missing(
            "1", "cannot list rulesets (does the admin token have Administration?)", [resp.meta()]
        )
    count = len(resp.body) if isinstance(resp.body, list) else 0
    return passed("1", "rulesets are available on the ledger repository's plan", [f"{count} found"])


@guarded("2")
def check_02_bypass_per_ruleset(ctx: Ctx) -> CheckResult:
    sets = load_rulesets(ctx)
    a, b = ruleset_a(sets), ruleset_b(sets)
    if a is None or b is None:
        raise Precondition("Ruleset A and/or B is missing or not active on cheeseless/budget/**")
    app = (ctx.config.app_id, "Integration", "always")
    problems: list[str] = []
    if a.bypass:
        problems.append("Ruleset A must have no bypass actors")
    if b.bypass != (app,):
        problems.append("Ruleset B bypass must be exactly the judge App, mode always")
    if problems:
        return failed("2", "; ".join(problems), [f"A bypass={len(a.bypass)}, B bypass={b.bypass}"])
    return passed("2", "A binds everyone; B lets only the judge App through", [a.name, b.name])


@guarded("3")
def check_03_ruleset_matrix(ctx: Ctx) -> CheckResult:
    repo = Repo(ctx.client, ctx.config.ledger_repo)
    require_b(ctx)
    if ruleset_a(load_rulesets(ctx)) is None:
        raise Precondition("Ruleset A (restrict deletion, block force push) is not active")
    _require_pusher_write(ctx, repo)
    tokens = _tokens(ctx)
    for name, credential in tokens.items():
        write_control(repo, credential, ctx.random_hex() + "-" + name)
    app = tokens["app"]
    rows: list[str] = []
    bad = 0
    for actor in ACTORS:
        token = tokens[actor]
        identity = actor_name(ctx, actor, token)
        for op in OPS:
            branch, base = _probe(ctx, repo, f"matrix-{actor}-{op}")
            tip = repo.child(base, app, "independent operation base")
            need(repo.update_ref(branch, tip, app, force=False), {200}, "prepare matrix cell")
            started = attempt_started()
            resp, ref = _attempt(ctx, repo, branch, base, op, actor, token, app)
            codes, text = expectation(actor, op)
            ok = (
                (
                    ruleset_rejection(ctx, repo, resp, ref=ref, actor=identity, started=started)
                    and resp.status in codes
                )
                if codes == REJECT
                else resp.status in codes
            )
            bad += 0 if ok else 1
            rows.append(
                f"{actor}/{op}: HTTP {resp.status}, expected {text}: {'ok' if ok else 'MISMATCH'}"
            )
    if bad:
        return failed("3", f"{bad} of {len(rows)} matrix cells differ from the expectation", rows)
    return passed("3", "every actor/operation cell matches; only the App fast-forwards", rows)


def _attempt(
    ctx: Ctx, repo: Repo, branch: str, base: str, op: str, actor: str, token: Token, app: Token
) -> tuple[Response, str]:
    tag = f"live-{ctx.random_hex()}"
    tip = repo.get_ref(branch, app)
    if op == "creation":
        branch = f"cheeseless/budget/{tag}-{actor}"
        return repo.create_ref(branch, base, token), f"refs/heads/{branch}"
    ref = f"refs/heads/{branch}"
    if tip is None:
        return Response(404), ref  # the scratch branch is gone: the cell cannot be a pass
    if op == "fast_forward":
        return repo.update_ref(branch, repo.child(tip, app, tag), token, force=False), ref
    if op == "force_update":
        return repo.update_ref(branch, repo.orphan(tip, app, tag), token, force=True), ref
    if op == "rewind":
        return repo.update_ref(branch, base, token, force=True), ref
    return repo.delete_ref(branch, token), ref


@guarded("4")
def check_04_concurrent_writers(ctx: Ctx) -> CheckResult:
    repo = Repo(ctx.client, ctx.config.ledger_repo)
    token = ctx.budget_token()
    app = ctx.app_token()
    branch, _ = _probe(ctx, repo, "race")
    rows: list[str] = []
    for rnd in range(ROUNDS):
        tip = repo.get_ref(branch, app)
        if tip is None:
            raise Precondition("the scratch branch disappeared")
        kids = [repo.child(tip, app, f"w{rnd}-{i}") for i in range(WRITERS)]
        calls = [(lambda sha=sha: repo.update_ref(branch, sha, token, force=False)) for sha in kids]
        results = ctx.parallel(calls)
        wins = [sha for sha, r in zip(kids, results, strict=True) if r.status == 200]
        losers = [r.status for r in results if r.status != 200]
        final = repo.get_ref(branch, app)
        rows.append(
            f"tip {rnd}: {len(wins)} winner(s), loser statuses {sorted(set(losers))}, "
            f"final tip matches winner: {wins[:1] == [final]}"
        )
        if len(wins) != 1 or any(s != 422 for s in losers) or final != wins[0]:
            return failed("4", f"round {rnd}: not exactly one winner for the tip", rows)
    return passed(
        "4", f"exactly one of {WRITERS} concurrent writers won each of {ROUNDS} tips", rows
    )


@guarded("5")
def check_05_read_after_update(ctx: Ctx) -> CheckResult:
    repo = Repo(ctx.client, ctx.config.ledger_repo)
    app = ctx.app_token()
    branch, base = _probe(ctx, repo, "read")
    new = repo.child(base, app, "read-after-update")
    need(repo.update_ref(branch, new, app, force=False), {200}, "the App's fast-forward")
    rows: list[str] = []
    for reader in ("app", "pusher"):
        token = app if reader == "app" else ctx.config.pusher_token
        seen = [repo.get_ref(branch, token) for _ in range(5)]
        rows.append(f"{reader}: {sum(1 for s in seen if s == new)} of 5 reads saw the update")
        if any(s != new for s in seen):
            return failed("5", f"a read by {reader} after a successful update was stale", rows)
    return passed("5", "reads right after a successful update reflect it", rows)


@guarded("12")
def check_12_creation_rejected(ctx: Ctx) -> CheckResult:
    repo = Repo(ctx.client, ctx.config.ledger_repo)
    require_b(ctx)
    _require_pusher_write(ctx, repo)
    write_control(repo, ctx.config.pusher_token, ctx.random_hex() + "-12")
    app = ctx.app_token()
    base = sha_of(
        need(repo.call("GET", f"git/ref/heads/{repo.default_branch(app)}", app), {200}, "base"),
        "object",
        "sha",
    )
    branch = f"cheeseless/budget/live-{ctx.random_hex()}-new"
    identity = actor_name(ctx, "pusher", ctx.config.pusher_token)
    started = attempt_started()
    resp = repo.create_ref(branch, base, ctx.config.pusher_token)
    if (
        ruleset_rejection(
            ctx, repo, resp, ref=f"refs/heads/{branch}", actor=identity, started=started
        )
        and resp.status in REJECT
    ):
        return passed("12", "branch creation is refused under Ruleset B", [resp.meta()])
    return failed("12", "creation was not refused by the ruleset", [resp.meta()])
