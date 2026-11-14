"""Reading and classifying the ledger rulesets (checks 1, 2, 12)."""

from __future__ import annotations

from dataclasses import dataclass

from tests.live.github_client import Response
from tests.live.model import Ctx
from tests.live.ref_ops import Precondition, Repo, need

BUDGET_REF = "refs/heads/cheeseless/budget/**"


@dataclass(frozen=True)
class Ruleset:
    name: str
    enforcement: str
    refs: tuple[str, ...]
    rules: frozenset[str]
    bypass: tuple[tuple[str, str, str], ...]

    def targets_budget(self) -> bool:
        return self.enforcement == "active" and BUDGET_REF in self.refs


def _parse(body: object) -> Ruleset | None:
    if not isinstance(body, dict):
        return None
    cond = body.get("conditions")
    ref_name = cond.get("ref_name") if isinstance(cond, dict) else None
    include = ref_name.get("include") if isinstance(ref_name, dict) else None
    rules = body.get("rules")
    actors = body.get("bypass_actors")
    if not (isinstance(include, list) and isinstance(rules, list) and isinstance(actors, list)):
        return None
    return Ruleset(
        name=str(body.get("name", "")),
        enforcement=str(body.get("enforcement", "")),
        refs=tuple(str(r) for r in include),
        rules=frozenset(str(r.get("type")) for r in rules if isinstance(r, dict)),
        bypass=tuple(
            (str(a.get("actor_id")), str(a.get("actor_type")), str(a.get("bypass_mode")))
            for a in actors
            if isinstance(a, dict)
        ),
    )


def list_response(ctx: Ctx) -> Response:
    return Repo(ctx.client, ctx.config.ledger_repo).call("GET", "rulesets", ctx.config.admin_token)


def load_rulesets(ctx: Ctx) -> list[Ruleset]:
    """Every parsable ruleset of the ledger repository, read with the admin token."""
    repo = Repo(ctx.client, ctx.config.ledger_repo)
    listing = need(list_response(ctx), {200}, "list ledger rulesets")
    items = listing.body if isinstance(listing.body, list) else []
    found: list[Ruleset] = []
    for item in items:
        rid = item.get("id") if isinstance(item, dict) else None
        if not isinstance(rid, int):
            continue
        detail = need(
            repo.call("GET", f"rulesets/{rid}", ctx.config.admin_token), {200}, "read a ruleset"
        )
        parsed = _parse(detail.body)
        if parsed is not None:
            found.append(parsed)
    return found


def ruleset_a(sets: list[Ruleset]) -> Ruleset | None:
    for s in sets:
        if s.targets_budget() and {"deletion", "non_fast_forward"} <= s.rules:
            return s
    return None


def ruleset_b(sets: list[Ruleset]) -> Ruleset | None:
    for s in sets:
        if s.targets_budget() and {"creation", "update"} <= s.rules:
            return s
    return None


def require_b(ctx: Ctx) -> Ruleset:
    found = ruleset_b(load_rulesets(ctx))
    if found is None:
        raise Precondition("Ruleset B (restrict creation and update) is not active")
    return found
