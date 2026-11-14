"""Offline tests for scripts/live_gate_setup.py: dry run by default, --apply gated, canary only."""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
import threading
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "live_gate_setup", ROOT / "scripts/live_gate_setup.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


setup = _load()


@pytest.fixture(autouse=True)
def ownership_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup, "OWNERSHIP_FILE", tmp_path / "ownership.json")


class FakeApi:
    """In-memory GitHub: repos, rulesets, files, environment policies."""

    def __init__(self, existing: tuple[str, ...] = ()) -> None:
        self.repos = set(existing)
        self.rulesets: dict[str, list[dict[str, Any]]] = {}
        self.files: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.protection: dict[str, Any] = {}
        self.policies: list[dict[str, Any]] = [{"id": 1, "name": "stray"}]

    def call(self, method: str, path: str, body: object = None) -> tuple[int, Any]:
        self.calls.append((method, path))
        parts = path.split("/")
        if parts[0] == "repos":
            full = "/".join(parts[1:3])
            rest = "/".join(parts[3:])
            known = full in self.repos
            if method == "GET" and rest == "":
                return (
                    (200, {"default_branch": "main", "id": sum(full.encode())})
                    if known
                    else (404, None)
                )
            if method == "DELETE" and rest == "":
                self.deleted.append(full)
                self.repos.discard(full)
                return 204, None
            if rest == "rulesets":
                if method == "GET":
                    return 200, self.rulesets.get(full, [])
                self.rulesets.setdefault(full, []).append(dict(body))  # type: ignore[arg-type]
                return 201, None
            if rest.startswith("contents/"):
                key = (full, rest.split("?")[0].removeprefix("contents/"))
                if method == "GET":
                    return 404, None
                self.files[key] = "put"
                return 201, None
            if rest.endswith("/protection"):
                self.protection[full] = body
                return 200, None
            if "deployment-branch-policies" in rest:
                if method == "GET":
                    return 200, {"branch_policies": self.policies}
                if method == "DELETE":
                    self.policies = [p for p in self.policies if str(p["id"]) != parts[-1]]
                    return 204, None
                self.policies.append(dict(body))  # type: ignore[arg-type]
                return 201, None
            if "/environments/" in path:
                return 200, None
        if method == "POST" and path in {"user/repos", "orgs/o/repos"}:
            self.repos.add(f"o/{body['name']}")  # type: ignore[index]
            return 201, {"id": sum(f"o/{body['name']}".encode())}
        return 404, None


class FakeSecrets:
    def __init__(self) -> None:
        self.written: list[tuple[str, str, str]] = []

    def set(self, repo: str, name: str, value: str, *, environment: str) -> None:
        self.written.append((repo, name, environment))


def _plan(**kw: Any) -> Any:
    return setup.Plan("o", "t", kw.pop("app_id", 77), **kw)


def _run(api: FakeApi, secrets: FakeSecrets, *, apply: bool, **kw: Any) -> list[str]:
    lines: list[str] = []
    setup.run_setup(api, secrets, _plan(**kw), apply=apply, say=lines.append)
    return lines


def test_dry_run_mutates_nothing() -> None:
    api, sec = FakeApi(), FakeSecrets()
    lines = _run(api, sec, apply=False)
    assert all(m == "GET" for m, _ in api.calls)
    assert not sec.written and not api.repos
    assert any("dry run" in line for line in lines)


def test_apply_creates_everything_private() -> None:
    api, sec = FakeApi(), FakeSecrets()
    _run(api, sec, apply=True)
    assert api.repos == {f"o/cheeseless-canary-t-{r}" for r in ("ledger", "judged", "fresh")}
    names = {r["name"] for r in api.rulesets["o/cheeseless-canary-t-ledger"]}
    assert names == {"cheeseless-budget-A", "cheeseless-budget-B"}
    assert sec.written == [("o/cheeseless-canary-t-judged", setup.SECRET_NAME, "cheeseless-judge")]
    guard = api.protection["o/cheeseless-canary-t-judged"]
    assert guard["required_status_checks"]["checks"] == [
        {"context": "cheeseless judge", "app_id": 77}
    ]
    assert [p["name"] for p in api.policies] == ["main"]


def test_rulesets_shape() -> None:
    sets = setup._rulesets(_plan())
    a, b = sets["cheeseless-budget-A"], sets["cheeseless-budget-B"]
    assert a["bypass_actors"] == []
    assert {r["type"] for r in a["rules"]} == {"deletion", "non_fast_forward"}
    assert b["bypass_actors"] == [
        {"actor_id": 77, "actor_type": "Integration", "bypass_mode": "always"}
    ]
    assert {r["type"] for r in b["rules"]} == {"creation", "update"}


def test_apply_is_idempotent() -> None:
    api, sec = FakeApi(), FakeSecrets()
    _run(api, sec, apply=True)
    before = {k: len(v) for k, v in api.rulesets.items()}
    _run(api, sec, apply=True)
    assert {k: len(v) for k, v in api.rulesets.items()} == before
    assert len(api.repos) == 3


def test_secret_value_never_printed() -> None:
    lines = _run(FakeApi(), FakeSecrets(), apply=True)
    assert not any(len(tok) >= 32 and tok.isalnum() for line in lines for tok in line.split())


def test_rulesets_need_app_id() -> None:
    with pytest.raises(setup.SetupError, match="app-id"):
        setup.ensure_rulesets(
            FakeApi({"o/cheeseless-canary-t-ledger"}), _plan(app_id=None), False, print
        )


def test_same_repo_adds_role() -> None:
    api = FakeApi()
    names = setup.run_setup(api, FakeSecrets(), _plan(same_repo=True), apply=True, say=print)
    assert "o/cheeseless-canary-t-same" in api.repos
    assert any(n.startswith("CHEESELESS_LIVE_SAME_REPO=") for n in names)


def test_teardown_only_canary() -> None:
    api = FakeApi({"o/cheeseless-canary-t-ledger", "o/keepme"})
    setup.run_teardown(api, _plan(), apply=True, say=lambda _: None)
    assert api.deleted == []
    assert "o/keepme" in api.repos


def test_teardown_dry_run_deletes_nothing() -> None:
    api = FakeApi({"o/cheeseless-canary-t-ledger"})
    setup.run_teardown(api, _plan(), apply=False, say=lambda _: None)
    assert not api.deleted


def test_refuses_non_canary_name() -> None:
    with pytest.raises(setup.SetupError):
        setup._canary("o/production")
    with pytest.raises(setup.SetupError):
        setup._canary("noowner")


def test_main_defaults_to_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    api = FakeApi()
    assert (
        setup.main(["setup", "--owner", "o", "--app-id", "7"], api=api, secrets_port=FakeSecrets())
        == 0
    )
    assert not api.repos
    assert "dry run" in capsys.readouterr().out


def test_main_reports_error_exit(capsys: pytest.CaptureFixture[str]) -> None:
    assert setup.main(["setup", "--owner", "o"], api=FakeApi(), secrets_port=FakeSecrets()) == 2
    assert "error:" in capsys.readouterr().err


def test_write_key_mode_and_no_overwrite(tmp_path: Path) -> None:
    key = tmp_path / "k.pem"
    setup.write_key(key, "PEM")
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        setup.write_key(key, "PEM")


def test_write_key_refuses_inside_git_repo(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    with pytest.raises(setup.SetupError, match="git"):
        setup.write_key(tmp_path / "k.pem", "PEM")


def test_manifest_dry_run_starts_nothing() -> None:
    lines: list[str] = []
    assert (
        setup.run_manifest(FakeApi(), "o", Path("/x"), org=False, apply=False, say=lines.append)
        is None
    )
    assert "would start" in lines[0]


def test_manifest_flow_end_to_end(tmp_path: Path) -> None:
    class Conv:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def call(self, method: str, path: str, body: object = None) -> tuple[int, Any]:
            self.calls.append(path)
            return 201, {"id": 42, "pem": "PEMDATA", "html_url": "https://x/apps/a"}

    seen: list[str] = []
    result: dict[str, Any] = {}
    key = tmp_path / "out" / "app.pem"
    key.parent.mkdir()

    def say(line: str) -> None:
        seen.append(line)
        if "/start" in line:
            url = line.rsplit(" ", 1)[1]
            page = urllib.request.urlopen(url, timeout=5).read().decode()  # noqa: S310
            assert "manifest" in page and "state=" in page
            state = page.split("state=")[1].split('"')[0]
            base = url.rsplit("/", 1)[0]
            bad = _status(f"{base}/callback?state=wrong&code=c")
            assert bad == 400
            urllib.request.urlopen(f"{base}/callback?state={state}&code=thecode", timeout=5)  # noqa: S310

    api = Conv()
    worker = threading.Thread(
        target=lambda: result.update(
            id=setup.run_manifest(
                api,
                "o",
                key,
                org=False,
                apply=True,
                say=say,
                wait=10,
                convert=lambda code: api.call("POST", f"app-manifests/{code}/conversions")[1],
            )
        )
    )
    worker.start()
    worker.join(15)
    assert result["id"] == 42
    assert api.calls == ["app-manifests/thecode/conversions"]
    assert key.read_text() == "PEMDATA"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert "PEMDATA" not in "\n".join(seen)


def _status(url: str) -> int:
    try:
        return urllib.request.urlopen(url, timeout=5).status  # noqa: S310
    except urllib.error.HTTPError as err:
        return err.code


def test_manifest_requests_least_permissions() -> None:
    data = setup.manifest("http://127.0.0.1:1/callback")
    assert data["default_permissions"] == {"checks": "write", "contents": "write"}
    assert data["public"] is False
    json.dumps(data)


def test_workflows_match_probe_expectations() -> None:
    assert "cheeseless-judge" in setup.JUDGE_WORKFLOW
    assert setup.SECRET_NAME in setup.JUDGE_WORKFLOW


def test_preexisting_repo_is_never_owned_or_deleted(tmp_path: Path) -> None:
    api = FakeApi(("o/cheeseless-canary-t-ledger", "o/cheeseless-canary-t-same"))
    path = tmp_path / "ownership.json"
    setup.run_setup(api, FakeSecrets(), _plan(), apply=True, say=lambda _: None, manifest_path=path)
    setup.run_teardown(api, _plan(), apply=True, say=lambda _: None, manifest_path=path)
    assert "o/cheeseless-canary-t-ledger" not in api.deleted
    assert "o/cheeseless-canary-t-same" not in api.deleted


def test_conversion_uses_https_without_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("conversion invoked a subprocess")

    monkeypatch.setattr(setup.subprocess, "run", forbidden)
    seen = []

    def opener(request: Any, *, timeout: float) -> Any:
        seen.append((request.full_url, request.method, timeout))
        return io.BytesIO(b'{"id": 42, "pem": "fixture"}')

    code = "".join(["one", "time"])
    assert setup.convert_manifest(code, opener=opener)["id"] == 42
    assert seen == [(f"https://api.github.com/app-manifests/{code}/conversions", "POST", 30)]


def test_teardown_deletes_owned_ids_only_and_preserves_ownership_on_rerun(tmp_path: Path) -> None:
    api = FakeApi()
    path = tmp_path / "ownership.json"
    for _ in range(2):
        setup.run_setup(
            api, FakeSecrets(), _plan(), apply=True, say=lambda _: None, manifest_path=path
        )
    data = json.loads(path.read_text())
    assert all(entry["created"] is True and isinstance(entry["id"], int) for entry in data.values())
    setup.run_teardown(api, _plan(), apply=False, say=lambda _: None, manifest_path=path)
    assert not api.deleted
    setup.run_teardown(api, _plan(), apply=True, say=lambda _: None, manifest_path=path)
    assert set(api.deleted) == set(data)


def test_teardown_refuses_replaced_repository_id(tmp_path: Path) -> None:
    full = "o/cheeseless-canary-t-ledger"
    api = FakeApi((full,))
    path = tmp_path / "ownership.json"
    path.write_text(json.dumps({full: {"id": 1, "created": True}}))
    with pytest.raises(setup.SetupError, match="ID changed"):
        setup.run_teardown(api, _plan(), apply=True, say=lambda _: None, manifest_path=path)
    assert not api.deleted


def test_teardown_refuses_noncanary_manifest_entry(tmp_path: Path) -> None:
    path = tmp_path / "ownership.json"
    path.write_text(json.dumps({"o/precious": {"id": 1, "created": True}}))
    api = FakeApi()
    with pytest.raises(setup.SetupError, match="not a"):
        setup.run_teardown(api, _plan(), apply=True, say=lambda _: None, manifest_path=path)
    assert not api.deleted


def test_conversion_errors_hide_code_and_response_body() -> None:
    code = "".join(["one", "time"])

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise ValueError(code + " response-private-material")

    with pytest.raises(setup.SetupError) as caught:
        setup.convert_manifest(code, opener=broken)
    assert code not in str(caught.value)
    assert "response-private-material" not in str(caught.value)
