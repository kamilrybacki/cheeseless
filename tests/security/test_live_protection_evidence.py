"""Offline adversarial attribution checks; a rejected request alone proves nothing."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.live.fake_github import Knobs
from tests.live.github_client import Response
from tests.live.model import Outcome
from tests.unit.test_live_checks import ALL, make_ctx


@pytest.mark.parametrize("number", [3, 12])
@pytest.mark.parametrize("fault", ["missing", "actor", "ref", "stale", "bypass", "malformed"])
def test_unrelated_rule_suites_cannot_attribute_rejection(monkeypatch, number, fault):
    ctx = make_ctx()
    original = ctx.client.request

    def request(method, path, *, token=None, json=None):
        resp = original(method, path, token=token, json=json)
        if "rulesets/rule-suites?" in path:
            if fault == "missing":
                return Response(200, [])
            if fault == "malformed":
                return Response(200, {"message": "not a suite list"})
            entries = [dict(entry) for entry in resp.body]
            for entry in entries:
                if fault == "actor":
                    entry["actor_name"] = "someone-else"
                elif fault == "ref":
                    entry["ref"] = "refs/heads/unrelated"
                elif fault == "stale":
                    entry["pushed_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
                elif fault == "bypass":
                    entry["result"] = "bypass"
            return Response(200, entries)
        return resp

    monkeypatch.setattr(ctx.client, "request", request)
    assert ALL[number](ctx).outcome is Outcome.PRECONDITION_MISSING


@pytest.mark.parametrize("number", [3, 8, 12])
def test_canary_successful_protected_write_fails(number):
    ctx = make_ctx(Knobs(rulesets_enforced=False, same_repo_protected=False))
    assert ALL[number](ctx).outcome is Outcome.FAIL


@pytest.mark.parametrize("fault", ["control", "protection"])
def test_plain_422_without_classic_protection_contrast_is_not_evidence(monkeypatch, fault):
    ctx = make_ctx()
    original = ctx.client.request

    def request(method, path, *, token=None, json=None):
        if method == "GET" and path.endswith("/protection") and fault == "protection":
            return Response(404)
        if method == "PUT" and (
            fault == "control" or isinstance(json, dict) and json.get("branch") == "main"
        ):
            return Response(422, {"message": "Reference update failed"})
        return original(method, path, token=token, json=json)

    monkeypatch.setattr(ctx.client, "request", request)
    assert ALL[8](ctx).outcome is Outcome.PRECONDITION_MISSING


@pytest.mark.parametrize("number", [3, 12])
def test_malformed_ref_request_without_suite_is_not_protection(monkeypatch, number):
    ctx = make_ctx()
    original = ctx.client.request

    def request(method, path, *, token=None, json=None):
        if (
            method == "POST"
            and path.endswith("git/refs")
            and token.label == "pusher"
            and str(json.get("ref")).startswith("refs/heads/cheeseless/budget/")
        ):
            # Do not call the fake: no rule evaluation took place.
            return Response(422, {"message": "Reference update failed"})
        return original(method, path, token=token, json=json)

    monkeypatch.setattr(ctx.client, "request", request)
    assert ALL[number](ctx).outcome is Outcome.PRECONDITION_MISSING
