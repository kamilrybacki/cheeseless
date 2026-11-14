"""Decision logic of the live release-gate checks, proven against an in-memory GitHub."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from tests.live import checks_counter as counter
from tests.live import checks_seed, checks_tokens, checks_workflow
from tests.live import config as live_config
from tests.live.fake_github import (
    LEDGER,
    FakeGitHub,
    FakeMinter,
    FakeSeeder,
    Knobs,
    make_config,
)
from tests.live.github_client import (
    ClientError,
    Response,
    Token,
    UrllibGitHubClient,
    guard_mutation,
)
from tests.live.model import CheckResult, Ctx, Outcome, run_threads
from tests.live.ref_ops import REJECT, poll
from tests.live.tokens import AppTokenMinter, build_jwt

Check = Callable[[Ctx], CheckResult]
ALL: dict[int, Check] = {
    1: counter.check_01_rulesets_available,
    2: counter.check_02_bypass_per_ruleset,
    3: counter.check_03_ruleset_matrix,
    4: counter.check_04_concurrent_writers,
    5: counter.check_05_read_after_update,
    6: checks_tokens.check_06_budget_token_scope,
    7: checks_tokens.check_07_verdict_token_scope,
    8: checks_tokens.check_08_same_repo_protection,
    9: checks_workflow.check_09_branch_workflow_blind,
    10: checks_workflow.check_10_judge_reads_secret,
    11: checks_workflow.check_11_required_check_source,
    12: counter.check_12_creation_rejected,
    13: checks_seed.check_13_seeding,
}


def make_ctx(
    knobs: Knobs | None = None,
    *,
    same: bool = True,
    seeder: FakeSeeder | None = None,
    minter: FakeMinter | None = None,
    parallel: Callable[..., list[Response]] = run_threads,
) -> Ctx:
    return Ctx(
        make_config(same=same),
        FakeGitHub(knobs),
        minter or FakeMinter(),
        seeder or FakeSeeder(),
        sleep=lambda _s: None,
        parallel=parallel,
        random_hex=lambda: "ab12cd34",
        poll_seconds=10.0,
    )


@pytest.mark.parametrize("number", sorted(ALL))
def test_every_check_passes_on_a_correct_setup(number: int) -> None:
    result = ALL[number](make_ctx())
    assert result.outcome is Outcome.PASS, result.render()


# (check, knob that breaks exactly that protection, expected outcome)
BROKEN = [
    (1, Knobs(plan_blocks_rulesets=True), Outcome.FAIL),
    (2, Knobs(ruleset_b_present=False), Outcome.PRECONDITION_MISSING),
    (2, Knobs(a_has_bypass=True), Outcome.FAIL),
    (2, Knobs(b_bypass_app=False), Outcome.FAIL),
    (3, Knobs(rulesets_enforced=False), Outcome.FAIL),
    (4, Knobs(lost_update=True), Outcome.FAIL),
    (5, Knobs(stale_reads=True), Outcome.FAIL),
    (6, Knobs(budget_reaches_judged=True), Outcome.FAIL),
    (7, Knobs(verdict_writes_contents=True), Outcome.FAIL),
    (7, Knobs(verdict_can_post=False), Outcome.PRECONDITION_MISSING),
    (8, Knobs(same_repo_protected=False), Outcome.FAIL),
    (9, Knobs(branch_reads_secret=True), Outcome.FAIL),
    (9, Knobs(actions_enabled=False), Outcome.PRECONDITION_MISSING),
    (9, Knobs(env_gate_blocks_branch=True), Outcome.PASS),
    (10, Knobs(judge_succeeds=False), Outcome.FAIL),
    (10, Knobs(canary_workflows=False), Outcome.PRECONDITION_MISSING),
    (11, Knobs(pin_to_app=False), Outcome.PRECONDITION_MISSING),
    (11, Knobs(actions_can_forge=True), Outcome.FAIL),
    (12, Knobs(rulesets_enforced=False), Outcome.FAIL),
    (13, Knobs(unreadable_orphan=True), Outcome.FAIL),
]


@pytest.mark.parametrize(("number", "knobs", "expected"), BROKEN)
def test_a_broken_protection_never_passes(number: int, knobs: Knobs, expected: Outcome) -> None:
    result = ALL[number](make_ctx(knobs))
    assert result.outcome is expected, result.render()


def test_seeding_refusal_fails_check_13() -> None:
    result = checks_seed.check_13_seeding(make_ctx(seeder=FakeSeeder(ok=False)))
    assert result.outcome is Outcome.FAIL


@pytest.mark.parametrize("number", sorted(set(ALL) - {8}))
def test_unusable_app_is_precondition_missing_for_every_check(number: int) -> None:
    result = ALL[number](make_ctx(minter=FakeMinter(unavailable=True)))
    assert result.outcome in {Outcome.PRECONDITION_MISSING, Outcome.PASS}
    if number not in {1, 2, 9, 10}:  # those four never need an App token
        assert result.outcome is Outcome.PRECONDITION_MISSING, result.render()


def test_pusher_without_ledger_access_is_precondition_not_a_rejection() -> None:
    knobs = Knobs(pusher_access=False)
    for number in (3, 12):
        result = ALL[number](make_ctx(knobs))
        assert result.outcome is Outcome.PRECONDITION_MISSING, result.render()
        assert "pass" not in result.outcome.value


def test_check_8_is_not_applicable_without_a_same_repo() -> None:
    assert checks_tokens.check_08_same_repo_protection(make_ctx(same=False)).outcome is (
        Outcome.NOT_APPLICABLE
    )


# ----- exact statuses ----------------------------------------------------------------------
@pytest.mark.parametrize("status", [403, 422])
def test_creation_rejection_passes_only_on_the_exact_statuses(status: int) -> None:
    ctx = make_ctx()
    original = ctx.client.request

    def spoof(method: str, path: str, *, token: Token | None = None, json: object = None):  # type: ignore[no-untyped-def]
        resp = original(method, path, token=token, json=json)
        if (
            method == "POST"
            and path.endswith("git/refs")
            and token
            and token.label == "pusher"
            and isinstance(json, dict)
            and str(json.get("ref")).startswith("refs/heads/cheeseless/budget/")
        ):
            return Response(status, {"message": "Repository rule violations found"})
        return resp

    ctx.client.request = spoof  # type: ignore[method-assign]
    assert counter.check_12_creation_rejected(ctx).outcome is Outcome.PASS


@pytest.mark.parametrize("status", [200, 201, 404, 409, 500])
def test_creation_with_any_other_status_is_not_a_pass(status: int) -> None:
    assert status not in REJECT
    ctx = make_ctx()
    original = ctx.client.request

    def spoof(method: str, path: str, *, token: Token | None = None, json: object = None):  # type: ignore[no-untyped-def]
        if (
            method == "POST"
            and path.endswith("git/refs")
            and token
            and token.label == "pusher"
            and isinstance(json, dict)
            and str(json.get("ref")).startswith("refs/heads/cheeseless/budget/")
        ):
            return Response(status, {"message": "x"})
        return original(method, path, token=token, json=json)

    ctx.client.request = spoof  # type: ignore[method-assign]
    assert counter.check_12_creation_rejected(ctx).outcome is not Outcome.PASS


def test_expectation_table_encodes_the_app_fast_forward_positive_case() -> None:
    for actor in ("budget", "app"):
        assert counter.expectation(actor, "fast_forward")[0] == {200}
        assert counter.expectation(actor, "creation")[0] == {201}
    for op in counter.OPS:
        assert counter.expectation("pusher", op)[0] == REJECT
    for op in ("force_update", "rewind", "deletion"):
        for actor in counter.ACTORS:
            assert counter.expectation(actor, op)[0] == REJECT


def test_matrix_covers_every_actor_and_operation() -> None:
    result = counter.check_03_ruleset_matrix(make_ctx())
    assert len(result.evidence) >= len(counter.ACTORS) * len(counter.OPS)


# ----- ten concurrent writers ---------------------------------------------------------------
def test_ten_concurrent_writers_have_exactly_one_winner_per_tip() -> None:
    ctx = make_ctx()
    seen: list[int] = []

    def parallel(calls):  # type: ignore[no-untyped-def]
        seen.append(len(calls))
        return run_threads(calls)

    ctx.parallel = parallel
    result = counter.check_04_concurrent_writers(ctx)
    assert result.outcome is Outcome.PASS
    assert seen == [counter.WRITERS] * counter.ROUNDS


def test_two_winners_for_one_tip_fail_the_check() -> None:
    ctx = make_ctx(Knobs(lost_update=True))
    assert counter.check_04_concurrent_writers(ctx).outcome is Outcome.FAIL


# ----- infrastructure -----------------------------------------------------------------------
def test_token_repr_and_str_never_show_the_value() -> None:
    token = Token("super-secret-value", "label")
    assert "super-secret-value" not in repr(token)
    assert "super-secret-value" not in str(token)
    assert token.reveal() == "super-secret-value"


def test_response_meta_reports_status_and_key_names_only() -> None:
    meta = Response(200, {"token": "abc-secret", "other": 1}).meta()
    assert "abc-secret" not in meta
    assert "200" in meta and "token" in meta


@pytest.mark.parametrize(
    "path",
    [
        "repos/octo/cheeseless-canary-x/git/refs",
        "app/installations/1/access_tokens",
        "app-manifests/abc/conversions",
        "installation/token",
    ],
)
def test_guard_allows_canary_scoped_writes(path: str) -> None:
    guard_mutation("POST", path)


@pytest.mark.parametrize(
    "path", ["repos/octo/precious/git/refs", "repos/octo/cheeseless/contents/x", "user/repos"]
)
def test_guard_refuses_writes_outside_canary_repos(path: str) -> None:
    with pytest.raises(ClientError):
        guard_mutation("PUT", path)


def test_guard_never_blocks_reads() -> None:
    guard_mutation("GET", "repos/octo/precious")


def test_client_requires_https() -> None:
    with pytest.raises(ValueError, match="https"):
        UrllibGitHubClient(api_url="http://example.invalid")


GOOD_ENV = {
    live_config.ENABLE: "1",
    live_config.APP_ID: "7",
    live_config.APP_KEY_FILE: "/tmp/k.pem",  # noqa: S108
    live_config.PUSHER_TOKEN: "tok-pusher",
    live_config.ADMIN_TOKEN: "tok-admin",
    live_config.LEDGER_REPO: "octo/cheeseless-canary-ledger",
    live_config.JUDGED_REPO: "octo/cheeseless-canary-judged",
    live_config.FRESH_REPO: "octo/cheeseless-canary-fresh",
    live_config.CANARY_SECRET: "CANARY_SECRET",
    live_config.LEDGER_INSTALLATION: "11",
    live_config.JUDGED_INSTALLATION: "12",
}


def test_config_loads_and_never_exposes_secret_values_in_errors() -> None:
    cfg = live_config.load_config(GOOD_ENV)
    assert cfg.ledger_name == "cheeseless-canary-ledger"
    broken = {k: v for k, v in GOOD_ENV.items() if k != live_config.PUSHER_TOKEN}
    with pytest.raises(live_config.ConfigError) as err:
        live_config.load_config(broken)
    assert live_config.PUSHER_TOKEN in str(err.value)
    assert "tok-admin" not in str(err.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (live_config.LEDGER_REPO, "octo/ledger"),
        (live_config.JUDGED_REPO, "octo/cheeseless-canary-ledger"),
        (live_config.APP_ID, "abc"),
        (live_config.CANARY_SECRET, "lower"),
        (live_config.LEDGER_INSTALLATION, "x"),
        (live_config.FRESH_REPO, "no-slash"),
    ],
)
def test_config_rejects_bad_values(name: str, value: str) -> None:
    with pytest.raises(live_config.ConfigError):
        live_config.load_config({**GOOD_ENV, name: value})


def test_enabled_requires_exactly_one() -> None:
    assert live_config.enabled({live_config.ENABLE: "1"})
    assert not live_config.enabled({live_config.ENABLE: "true"})
    assert not live_config.enabled({})


def test_jwt_is_rs256_and_verifies_with_the_public_key() -> None:
    import base64
    import json

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    jwt = build_jwt("99", pem, now=1000)
    head, claims, sig = jwt.split(".")

    def unb64(part: str) -> bytes:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

    assert json.loads(unb64(head))["alg"] == "RS256"
    body = json.loads(unb64(claims))
    assert body["iss"] == "99" and body["exp"] > body["iat"]
    key.public_key().verify(
        unb64(sig), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )


class _MintClient:
    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method: str, path: str, *, token: Token | None = None, json: object = None):  # type: ignore[no-untyped-def]
        self.calls.append((method, path, json))
        if method == "POST":
            return Response(self.status, {"token": "minted-secret"})
        return Response(204)


def _minter(tmp_path: Path, client: _MintClient) -> AppTokenMinter:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "k.pem"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return AppTokenMinter(
        client, app_id="5", key_file=path, installations={LEDGER: 3}, clock=lambda: 10.0
    )


def test_minter_mints_scoped_tokens_and_revokes_them_on_close(tmp_path: Path) -> None:
    client = _MintClient()
    minter = _minter(tmp_path, client)
    token = minter.mint(LEDGER, permissions={"contents": "write"}, repositories=["r"])
    assert "minted-secret" not in repr(token)
    assert client.calls[0][1] == "app/installations/3/access_tokens"
    assert client.calls[0][2] == {"permissions": {"contents": "write"}, "repositories": ["r"]}
    minter.close()
    assert client.calls[-1][:2] == ("DELETE", "installation/token")


def test_minter_failure_is_a_client_error_without_the_response_body(tmp_path: Path) -> None:
    minter = _minter(tmp_path, _MintClient(status=403))
    with pytest.raises(ClientError) as err:
        minter.mint(LEDGER)
    assert "minted-secret" not in str(err.value)


def test_poll_stops_when_true_and_when_the_budget_is_spent() -> None:
    sleeps: list[float] = []
    assert poll(lambda: True, sleep=sleeps.append, seconds=10)
    assert not poll(lambda: False, sleep=sleeps.append, seconds=10, every=5)
    assert sleeps == [5, 5]


def test_seeder_receives_the_judged_repo_id_for_the_fresh_repo() -> None:
    seeder = FakeSeeder()
    checks_seed.check_13_seeding(make_ctx(seeder=seeder))
    assert seeder.calls and seeder.calls[0][0].endswith("cheeseless-canary-fresh")
    assert seeder.calls[0][1].isdigit()


# ----- opt-in behaviour ---------------------------------------------------------------------
def test_live_suite_is_skipped_unless_enabled() -> None:
    env = {k: v for k, v in os.environ.items() if k != live_config.ENABLE}
    out = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/live",
            "-m",
            "live_github",
            "--no-cov",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stdout
    assert "13 skipped" in out.stdout
    assert time.monotonic() > 0


@pytest.mark.parametrize("number", [3, 6, 7, 8, 12])
def test_authentication_failure_never_proves_protection(number: int) -> None:
    ctx = make_ctx()
    original = ctx.client.request

    def reject(method, path, *, token=None, json=None):  # type: ignore[no-untyped-def]
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            return Response(401, {"message": "Bad credentials"})
        return original(method, path, token=token, json=json)

    ctx.client.request = reject  # type: ignore[method-assign]
    assert ALL[number](ctx).outcome is Outcome.PRECONDITION_MISSING


def test_existing_file_conflict_is_not_branch_protection() -> None:
    ctx = make_ctx()
    original = ctx.client.request

    def conflict(method, path, *, token=None, json=None):  # type: ignore[no-untyped-def]
        if method == "PUT" and isinstance(json, dict) and json.get("branch") == "main":
            return Response(409, {"message": "sha was not supplied"})
        return original(method, path, token=token, json=json)

    ctx.client.request = conflict  # type: ignore[method-assign]
    assert ALL[8](ctx).outcome is Outcome.PRECONDITION_MISSING


@pytest.mark.parametrize("state", ["cancelled", "failure"])
def test_workflow_infrastructure_failure_is_precondition(state: str) -> None:
    ctx = make_ctx()
    original = ctx.client.request

    def broken(method, path, *, token=None, json=None):  # type: ignore[no-untyped-def]
        if path.endswith("/jobs"):
            return Response(
                200,
                {
                    "jobs": [
                        {"name": n, "conclusion": state} for n in ("with-env", "no-env", "control")
                    ]
                },
            )
        return original(method, path, token=token, json=json)

    ctx.client.request = broken  # type: ignore[method-assign]
    assert ALL[9](ctx).outcome is Outcome.PRECONDITION_MISSING


def test_absent_forged_check_is_precondition() -> None:
    ctx = make_ctx()
    original = ctx.client.request

    def missing(method, path, *, token=None, json=None):  # type: ignore[no-untyped-def]
        if "/check-runs" in path and method == "GET":
            return Response(200, {"check_runs": []})
        return original(method, path, token=token, json=json)

    ctx.client.request = missing  # type: ignore[method-assign]
    assert ALL[11](ctx).outcome is Outcome.PRECONDITION_MISSING


def test_app_pin_requires_exact_id() -> None:
    from dataclasses import replace

    ctx = make_ctx()
    ctx.config = replace(ctx.config, app_id="424")
    assert ALL[11](ctx).outcome is Outcome.PRECONDITION_MISSING


@pytest.mark.parametrize("number,actor", [(3, "pusher"), (3, "budget"), (3, "app"), (6, "budget")])
def test_same_actor_must_pass_write_control(number: int, actor: str) -> None:
    ctx = make_ctx()
    original = ctx.client.request

    def no_control(method, path, *, token=None, json=None):  # type: ignore[no-untyped-def]
        if method == "PUT" and "/contents/control-" in path and token.label == actor:
            return Response(403, {"message": "Resource not accessible"})
        return original(method, path, token=token, json=json)

    ctx.client.request = no_control  # type: ignore[method-assign]
    assert ALL[number](ctx).outcome is Outcome.PRECONDITION_MISSING


@pytest.mark.parametrize("status", [401, 403, 422])
def test_matrix_generic_rejection_is_not_ruleset_evidence(status: int) -> None:
    ctx = make_ctx()
    original = ctx.client.request

    def generic(method, path, *, token=None, json=None):  # type: ignore[no-untyped-def]
        if method == "POST" and path.endswith("git/refs") and isinstance(json, dict):
            if (
                str(json.get("ref")).startswith("refs/heads/cheeseless/budget/")
                and token.label == "pusher"
            ):
                return Response(status, {"message": "Unrelated error"})
        return original(method, path, token=token, json=json)

    ctx.client.request = generic  # type: ignore[method-assign]
    assert ALL[3](ctx).outcome is Outcome.PRECONDITION_MISSING


def test_fake_models_existing_contents_conflict_separately() -> None:
    from tests.live.fake_github import SAME
    from tests.live.ref_ops import Repo

    ctx = make_ctx(Knobs(same_repo_protected=False))
    repo = Repo(ctx.client, SAME)
    actor = ctx.minter.mint(SAME)
    assert checks_tokens.put_contents(repo, actor, "fixture.txt").status == 201
    conflict = checks_tokens.put_contents(repo, actor, "fixture.txt")
    assert conflict.status == 409
    assert "sha" in str(conflict.get("message"))


@pytest.mark.parametrize("number", [3, 8, 12])
def test_protection_evidence_passes_without_message_wording(number: int, monkeypatch) -> None:
    ctx = make_ctx()
    original = ctx.client.request

    def request(method, path, *, token=None, json=None):
        resp = original(method, path, token=token, json=json)
        if method in {"POST", "PATCH", "DELETE", "PUT"} and resp.status in {403, 409, 422}:
            return Response(resp.status, {"message": "Reference update failed"})
        return resp

    monkeypatch.setattr(ctx.client, "request", request)
    assert ALL[number](ctx).outcome is Outcome.PASS


def test_rule_suite_poll_waits_for_admin_visible_evidence(monkeypatch) -> None:
    ctx = make_ctx()
    original = ctx.client.request
    probes = 0
    sleeps = []
    ctx.sleep = sleeps.append

    def request(method, path, *, token=None, json=None):
        nonlocal probes
        resp = original(method, path, token=token, json=json)
        if "rulesets/rule-suites?" in path:
            assert token is ctx.config.admin_token
            assert "time_period=day" in path and "per_page=100" in path
            probes += 1
            if probes == 1:
                return Response(200, [])
        return resp

    monkeypatch.setattr(ctx.client, "request", request)
    assert ALL[12](ctx).outcome is Outcome.PASS
    assert probes == 2 and sleeps == [5.0]


def test_fake_rule_suites_restrict_reader_and_record_bypass() -> None:
    from tests.live.ref_ops import Repo

    ctx = make_ctx()
    repo = Repo(ctx.client, LEDGER)
    base = repo.get_ref("main", ctx.app_token())
    assert base is not None
    branch = "cheeseless/budget/suite-control"
    assert repo.create_ref(branch, base, ctx.config.pusher_token).body == {
        "message": "Reference update failed"
    }
    assert repo.create_ref(branch, base, ctx.app_token()).status == 201
    path = "rulesets/rule-suites?ref=refs%2Fheads%2Fcheeseless%2Fbudget%2Fsuite-control"
    assert repo.call("GET", path, ctx.config.pusher_token).status == 403
    entries = repo.call("GET", path, ctx.config.admin_token).body
    assert [(entry["actor_name"], entry["result"]) for entry in entries] == [
        ("judge[bot]", "bypass"),
        ("pusher-login", "fail"),
    ]


@pytest.mark.parametrize(
    "response", [Response(200, {"slug": "judge"}), Response(403), Response(200)]
)
def test_app_actor_identity_uses_authenticated_app(tmp_path, monkeypatch, response) -> None:
    client = _MintClient()
    minter = _minter(tmp_path, client)

    def request(method, path, *, token=None, json=None):
        assert (method, path) == ("GET", "app")
        assert token.label == "app-jwt"
        return response

    monkeypatch.setattr(client, "request", request)
    if response.get("slug"):
        assert minter.actor_name() == "judge[bot]"
    else:
        with pytest.raises(ClientError, match="authenticated App identity"):
            minter.actor_name()
