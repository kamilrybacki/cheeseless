"""Release audit: redacted metadata only, fail closed, archives checked.

The scanner used here is a deterministic local stand-in for the Gitleaks CLI: it
emits the same JSON shape, exits nonzero on a simulated scanner failure, and
detects one synthetic canary.  The real pinned scanner is exercised by the CI
release-audit job and by the manual release run, so these tests can stay offline
and independent of a downloaded binary.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess  # noqa: S404 - fixed git argv in tests
import sys
import tarfile
import textwrap
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
CANARY = "RELEASE-AUDIT-" + "CANARY-4f9a2c"
DENY_VALUE = "PRIVATE-ID-abcdef12"

FAKE_SCANNER = textwrap.dedent(
    f"""
    #!/usr/bin/env python3
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    MARKER = {CANARY!r}
    args = sys.argv[1:]
    if args == ["version"]:
        print(os.environ.get("FAKE_GITLEAKS_VERSION", "8.30.1"))
        raise SystemExit(0)
    if os.environ.get("FAKE_GITLEAKS_FAIL") == "1":
        print("simulated scanner failure " + MARKER, file=sys.stderr)
        raise SystemExit(2)
    mode, target = args[0], Path(args[1])
    report = Path(args[args.index("--report-path") + 1])
    findings = []
    if mode == "dir":
        files = (
            [target]
            if target.is_file()
            else sorted(p for p in target.rglob("*") if p.is_file())
        )
        for path in files:
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if MARKER.encode() in data:
                line = data[: data.index(MARKER.encode())].count(b"\\n") + 1
                relative = path.relative_to(target).as_posix() if target.is_dir() else path.name
                findings.append(
                    {{
                        "RuleID": "generic-api-key",
                        "File": relative,
                        "StartLine": line,
                        "Secret": "REDACTED",
                        "Match": "REDACTED",
                        "Commit": "",
                    }}
                )
    elif mode == "git":
        out = subprocess.run(
            ["git", "-C", str(target), "log", "--all", "-p", "--format=commit %H"],
            capture_output=True,
            text=True,
        )
        text = out.stdout
        if MARKER in text:
            commit = ""
            for line in text.splitlines():
                if line.startswith("commit "):
                    commit = line.split()[1]
                    break
            findings.append(
                {{
                    "RuleID": "generic-api-key",
                    "File": "history",
                    "StartLine": 1,
                    "Secret": "REDACTED",
                    "Match": "REDACTED",
                    "Commit": commit,
                }}
            )
    report.write_text(json.dumps(findings))
    raise SystemExit(0)
    """
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_audit", ROOT / "scripts" / "release_audit.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git_env() -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": "Release Audit Test",
        "GIT_AUTHOR_EMAIL": "audit@example.invalid",
        "GIT_COMMITTER_NAME": "Release Audit Test",
        "GIT_COMMITTER_EMAIL": "audit@example.invalid",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed git argv in tests
        ["git", *args],  # noqa: S607
        cwd=root,
        env={**os.environ, **_git_env()},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "--initial-branch=main")
    for name, text in (files or {"notes.txt": "clean\n"}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def _scanner(tmp_path: Path) -> Path:
    path = tmp_path / "fake-gitleaks"
    path.write_text(FAKE_SCANNER.lstrip("\n"))
    path.chmod(0o755)
    return path


def _pin_fake_scanner(module: ModuleType, scanner: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        module.GITLEAKS_BINARY_SHA256,
        (module.platform.system(), module.platform.machine()),
        hashlib.sha256(scanner.read_bytes()).hexdigest(),
    )


def _wheel(path: Path, members: dict[str, bytes | str]) -> Path:
    with zipfile.ZipFile(path, "w") as handle:
        for name, content in members.items():
            data = content.encode() if isinstance(content, str) else content
            handle.writestr(name, data)
    return path


def _sdist(path: Path, members: dict[str, bytes | str]) -> Path:
    with tarfile.open(path, "w:gz") as handle:
        for name, content in members.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(name)
            info.size = len(data)
            handle.addfile(info, io.BytesIO(data))
    return path


def _clean_archives(tmp_path: Path) -> tuple[Path, Path]:
    wheel = _wheel(
        tmp_path / "clean-0.0.0-py3-none-any.whl",
        {"cheeseless/__init__.py": "", "cheeseless/clean.txt": "clean\n"},
    )
    sdist = _sdist(
        tmp_path / "clean-0.0.0.tar.gz",
        {"clean-0.0.0/pyproject.toml": "[build-system]\n", "clean-0.0.0/README.md": "# clean\n"},
    )
    return wheel, sdist


def _run(
    module: ModuleType,
    tmp_path: Path,
    repo: Path,
    scanner: Path,
    *,
    wheel: Path | None = None,
    sdist: Path | None = None,
    report_dir: Path | None = None,
    denylist: Path | None = None,
    release: bool = False,
    remote_refs_attested: bool = False,
) -> int:
    if wheel is None or sdist is None:
        wheel, sdist = _clean_archives(tmp_path)
    args = [
        "--repo",
        str(repo),
        "--gitleaks",
        str(scanner),
        "--report-dir",
        str(report_dir or (tmp_path / "report")),
        "--wheel",
        str(wheel),
        "--sdist",
        str(sdist),
    ]
    if denylist is not None:
        args.extend(["--denylist", str(denylist)])
    if release:
        args.append("--release")
    if remote_refs_attested:
        args.append("--remote-refs-attested")
    return int(module.main(args))


def _report(tmp_path: Path) -> dict[str, object]:
    paths = sorted((tmp_path / "report").glob("release-audit-*.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text())


def test_clean_repo_and_archives_pass_with_private_metadata_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    wheel, sdist = _clean_archives(tmp_path)
    before = _git(repo, "for-each-ref", "--format=%(refname)")

    assert _run(module, tmp_path, repo, _scanner(tmp_path), wheel=wheel, sdist=sdist) == 0

    out = capsys.readouterr().out
    assert "release audit: CLEAN" in out
    assert _git(repo, "for-each-ref", "--format=%(refname)") == before
    report = _report(tmp_path)
    assert report["status"] == "clean"
    assert len(report["local_refs"]) == 1
    assert report["local_refs"][0].startswith("ref:")
    limitations = " ".join(report["limitations"])
    assert "refs/pull/1" in limitations and "refs/pull/2" in limitations
    path = next((tmp_path / "report").glob("release-audit-*.json"))
    assert stat.S_IMODE((tmp_path / "report").stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_history_only_hit_is_blocked_and_redacted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    (repo / "leak.txt").write_text(f"token = {CANARY}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"add {CANARY}")
    (repo / "leak.txt").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove leak")
    assert CANARY not in (repo / "notes.txt").read_text()

    assert _run(module, tmp_path, repo, _scanner(tmp_path)) == 1

    out = capsys.readouterr().out
    assert "release audit: BLOCKED" in out
    assert "rule=generic-api-key" in out
    assert "commit=" in out
    assert CANARY not in out
    report_text = next((tmp_path / "report").glob("release-audit-*.json")).read_text()
    assert CANARY not in report_text
    assert '"Secret"' not in report_text and '"Match"' not in report_text


def test_artifact_only_hit_is_blocked_and_redacted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    _, sdist = _clean_archives(tmp_path)
    wheel = _wheel(
        tmp_path / "dirty-0.0.0-py3-none-any.whl",
        {"cheeseless/__init__.py": "", "cheeseless/canary.txt": f"token = {CANARY}\n"},
    )

    assert _run(module, tmp_path, repo, _scanner(tmp_path), wheel=wheel, sdist=sdist) == 1

    out = capsys.readouterr().out
    assert "release audit: BLOCKED" in out
    assert "path=scanner-file:" in out
    assert CANARY not in out
    assert CANARY not in next((tmp_path / "report").glob("release-audit-*.json")).read_text()


def test_missing_scanner_fails_closed_without_raw_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    wheel, sdist = _clean_archives(tmp_path)
    code = _run(
        module,
        tmp_path,
        repo,
        tmp_path / "does-not-exist",
        wheel=wheel,
        sdist=sdist,
    )
    out = capsys.readouterr().out
    assert code == 2
    assert "FAILED CLOSED (gitleaks-missing)" in out
    assert CANARY not in out


def test_incompatible_scanner_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    wheel, sdist = _clean_archives(tmp_path)
    monkeypatch.setenv("FAKE_GITLEAKS_VERSION", "9.9.9")

    code = _run(module, tmp_path, repo, _scanner(tmp_path), wheel=wheel, sdist=sdist)

    out = capsys.readouterr().out
    assert code == 2
    assert "FAILED CLOSED (gitleaks-incompatible)" in out


def test_default_mode_notes_an_unverified_scanner_digest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)

    assert _run(module, tmp_path, repo, _scanner(tmp_path)) == 0

    assert "release audit: CLEAN" in capsys.readouterr().out
    limitations = " ".join(_report(tmp_path)["limitations"])
    assert "SHA-256" in limitations
    assert "unverified" in limitations


def test_release_mode_rejects_a_same_version_unpinned_scanner(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)

    code = _run(module, tmp_path, repo, _scanner(tmp_path), release=True)

    out = capsys.readouterr().out
    assert code == 2
    assert "FAILED CLOSED (gitleaks-digest-mismatch)" in out


def test_release_requires_a_denylist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    scanner = _scanner(tmp_path)
    _pin_fake_scanner(module, scanner, monkeypatch)

    code = _run(module, tmp_path, repo, scanner, release=True)

    out = capsys.readouterr().out
    assert code == 2
    assert "FAILED CLOSED (denylist-required)" in out


def test_release_rejects_an_empty_denylist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    scanner = _scanner(tmp_path)
    _pin_fake_scanner(module, scanner, monkeypatch)
    denylist = tmp_path / "empty-denylist.toml"
    denylist.write_text("entries = []\n")

    code = _run(module, tmp_path, repo, scanner, denylist=denylist, release=True)

    out = capsys.readouterr().out
    assert code == 2
    assert "FAILED CLOSED (denylist-required)" in out


def test_release_without_remote_refs_attestation_is_blocked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    scanner = _scanner(tmp_path)
    _pin_fake_scanner(module, scanner, monkeypatch)

    code = _run(
        module,
        tmp_path,
        repo,
        scanner,
        denylist=_operator_denylist(tmp_path),
        release=True,
    )

    out = capsys.readouterr().out
    report = _report(tmp_path)
    assert code == 1
    assert "release audit: BLOCKED" in out
    assert "rule=remote-refs-unattested" in out
    assert report["release_mode"] is True
    assert report["remote_refs_attested"] is False
    assert [item["rule"] for item in report["findings"]] == ["remote-refs-unattested"]


def test_release_with_attestation_passes_a_clean_repo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    scanner = _scanner(tmp_path)
    _pin_fake_scanner(module, scanner, monkeypatch)

    code = _run(
        module,
        tmp_path,
        repo,
        scanner,
        denylist=_operator_denylist(tmp_path),
        release=True,
        remote_refs_attested=True,
    )

    out = capsys.readouterr().out
    report = _report(tmp_path)
    assert code == 0
    assert "release audit: CLEAN" in out
    assert report["status"] == "clean"
    assert report["release_mode"] is True
    assert report["remote_refs_attested"] is True


def test_default_mode_clean_repo_without_denylist_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)

    assert _run(module, tmp_path, repo, _scanner(tmp_path)) == 0

    out = capsys.readouterr().out
    report = _report(tmp_path)
    assert "release audit: CLEAN" in out
    assert report["release_mode"] is False
    assert report["remote_refs_attested"] is False


def test_scanner_error_fails_closed_without_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    wheel, sdist = _clean_archives(tmp_path)
    monkeypatch.setenv("FAKE_GITLEAKS_FAIL", "1")

    code = _run(module, tmp_path, repo, _scanner(tmp_path), wheel=wheel, sdist=sdist)

    captured = capsys.readouterr()
    assert code == 2
    assert "FAILED CLOSED (gitleaks-scan-failed)" in captured.out
    assert CANARY not in captured.out and CANARY not in captured.err


def test_report_directory_inside_repository_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    wheel, sdist = _clean_archives(tmp_path)

    code = _run(
        module,
        tmp_path,
        repo,
        _scanner(tmp_path),
        wheel=wheel,
        sdist=sdist,
        report_dir=repo / "audit-report",
    )

    out = capsys.readouterr().out
    assert code == 2
    assert "FAILED CLOSED (report-inside-repository)" in out
    assert not (repo / "audit-report").exists()


def test_unsafe_archive_member_blocks_without_echoing_the_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    _, sdist = _clean_archives(tmp_path)
    wheel = _wheel(
        tmp_path / "unsafe-0.0.0-py3-none-any.whl",
        {"cheeseless/__init__.py": "", "../escape.txt": f"{CANARY}\n"},
    )

    assert _run(module, tmp_path, repo, _scanner(tmp_path), wheel=wheel, sdist=sdist) == 1

    out = capsys.readouterr().out
    assert "rule=archive-unsafe-member" in out
    assert "../escape.txt" not in out
    assert CANARY not in out


def test_sensitive_and_private_state_members_are_not_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    _, sdist = _clean_archives(tmp_path)
    wheel = _wheel(
        tmp_path / "state-0.0.0-py3-none-any.whl",
        {
            "cheeseless/__init__.py": "",
            ".env": f"{CANARY}\n",
            ".import_linter_cache/cache.json": f"{CANARY}\n",
        },
    )

    assert _run(module, tmp_path, repo, _scanner(tmp_path), wheel=wheel, sdist=sdist) == 1

    out = capsys.readouterr().out
    assert "rule=sensitive-path" in out
    assert "rule=private-state" in out
    assert CANARY not in out


def test_missing_app_module_in_wheel_is_a_hygiene_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(
        tmp_path,
        {
            "src/cheeseless/__init__.py": "",
            "src/cheeseless/core.py": "VALUE = 1\n",
        },
    )
    wheel = _wheel(
        tmp_path / "partial-0.0.0-py3-none-any.whl",
        {"cheeseless/__init__.py": ""},
    )
    sdist = _sdist(
        tmp_path / "partial-0.0.0.tar.gz",
        {
            "partial-0.0.0/src/cheeseless/__init__.py": "",
            "partial-0.0.0/src/cheeseless/core.py": "VALUE = 1\n",
        },
    )

    assert _run(module, tmp_path, repo, _scanner(tmp_path), wheel=wheel, sdist=sdist) == 1

    out = capsys.readouterr().out
    assert "rule=archive-missing-module" in out
    assert "path=module:" in out


def test_nested_archive_depth_cap_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    inner = _wheel(tmp_path / "inner.whl", {"clean.txt": "clean\n"})
    middle = _wheel(tmp_path / "middle.zip", {"inner.whl": inner.read_bytes()})
    outer = _wheel(tmp_path / "outer.zip", {"middle.zip": middle.read_bytes()})
    wheel = _wheel(
        tmp_path / "nested-0.0.0-py3-none-any.whl",
        {"cheeseless/__init__.py": "", "outer.zip": outer.read_bytes()},
    )
    _, sdist = _clean_archives(tmp_path)

    assert _run(module, tmp_path, repo, _scanner(tmp_path), wheel=wheel, sdist=sdist) == 1

    assert "rule=archive-depth-cap" in capsys.readouterr().out


def test_operator_denylist_reports_rule_and_location_without_the_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path, {"notes.txt": f"operator identifier: {DENY_VALUE}\n"})
    denylist = tmp_path / "operator-denylist.toml"
    denylist.write_text(
        textwrap.dedent(
            """
            [[entries]]
            id = "operator-private-id"
            pattern = '''PRIVATE-ID-[0-9a-f]{8}'''
            """
        )
    )

    assert _run(module, tmp_path, repo, _scanner(tmp_path), denylist=denylist) == 1

    out = capsys.readouterr().out
    assert "rule=operator-private-id" in out
    assert "path=tracked-file:" in out
    assert DENY_VALUE not in out
    assert DENY_VALUE not in next((tmp_path / "report").glob("release-audit-*.json")).read_text()


def _operator_denylist(tmp_path: Path) -> Path:
    denylist = tmp_path / "operator-denylist.toml"
    denylist.write_text(
        textwrap.dedent(
            """
            [[entries]]
            id = "operator-private-id"
            pattern = '''PRIVATE-ID-[0-9a-f]{8}'''
            """
        )
    )
    return denylist


def test_denylist_applies_to_deleted_historical_blob(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(
        tmp_path,
        {"notes.txt": "clean\n", "old.txt": f"operator identifier: {DENY_VALUE}\n"},
    )
    (repo / "old.txt").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove old data")
    assert DENY_VALUE not in (repo / "notes.txt").read_text()

    assert (
        _run(
            module,
            tmp_path,
            repo,
            _scanner(tmp_path),
            denylist=_operator_denylist(tmp_path),
        )
        == 1
    )

    captured = capsys.readouterr()
    assert "rule=operator-private-id" in captured.out
    assert DENY_VALUE not in captured.out and DENY_VALUE not in captured.err
    report_text = next((tmp_path / "report").glob("release-audit-*.json")).read_text()
    assert DENY_VALUE not in report_text


def test_identical_history_content_under_a_sensitive_path_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path, {"clean.txt": "shared historical value\n"})
    (repo / ".env").write_text("shared historical value\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add a sensitive name")
    (repo / ".env").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove the sensitive name")
    assert not (repo / ".env").exists()

    assert _run(module, tmp_path, repo, _scanner(tmp_path)) == 1

    out = capsys.readouterr().out
    assert "rule=history-sensitive-path" in out
    assert ".env" not in out and ".env" not in json.dumps(_report(tmp_path))


def test_identical_history_content_under_a_denylisted_path_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path, {"clean.txt": "shared historical value\n"})
    denylist = tmp_path / "operator-denylist.toml"
    denylist.write_text(
        textwrap.dedent(
            """
            [[entries]]
            id = "operator-private-name"
            pattern = '''personal-secret'''
            """
        )
    )
    (repo / "personal-secret.txt").write_text("shared historical value\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add a denylisted name")
    (repo / "personal-secret.txt").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove the denylisted name")

    assert _run(module, tmp_path, repo, _scanner(tmp_path), denylist=denylist) == 1

    out = capsys.readouterr().out
    assert "rule=operator-private-name" in out
    assert "personal-secret" not in out
    assert "personal-secret" not in json.dumps(_report(tmp_path))


def test_denylist_applies_to_extracted_archive_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    _, sdist = _clean_archives(tmp_path)
    wheel = _wheel(
        tmp_path / "deny-0.0.0-py3-none-any.whl",
        {
            "cheeseless/__init__.py": "",
            "cheeseless/data.txt": f"operator identifier: {DENY_VALUE}\n",
        },
    )

    assert (
        _run(
            module,
            tmp_path,
            repo,
            _scanner(tmp_path),
            wheel=wheel,
            sdist=sdist,
            denylist=_operator_denylist(tmp_path),
        )
        == 1
    )

    captured = capsys.readouterr()
    assert "rule=operator-private-id" in captured.out
    assert DENY_VALUE not in captured.out and DENY_VALUE not in captured.err
    assert DENY_VALUE not in next((tmp_path / "report").glob("release-audit-*.json")).read_text()


def _assert_canary_absent(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, value: str = CANARY
) -> str:
    captured = capsys.readouterr()
    assert value not in captured.out and value not in captured.err
    for path in (tmp_path / "report").glob("release-audit-*.json"):
        assert value not in path.read_text()
    return captured.out


def test_tracked_sensitive_filename_is_not_emitted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path, {"notes.txt": "clean\n", f".env.{CANARY}": "clean value\n"})

    assert _run(module, tmp_path, repo, _scanner(tmp_path)) == 1

    out = _assert_canary_absent(capsys, tmp_path)
    assert "rule=sensitive-path" in out


def test_scanner_file_field_is_not_emitted_verbatim(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(
        tmp_path,
        {"notes.txt": "clean\n", f"{CANARY}-notes.txt": f"token = {CANARY}\n"},
    )

    assert _run(module, tmp_path, repo, _scanner(tmp_path)) == 1

    out = _assert_canary_absent(capsys, tmp_path)
    assert "rule=generic-api-key" in out


def test_archive_member_name_is_not_emitted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    _, sdist = _clean_archives(tmp_path)
    wheel = _wheel(
        tmp_path / "name-0.0.0-py3-none-any.whl",
        {
            "cheeseless/__init__.py": "",
            f".env.{CANARY}": "clean value\n",
            f"cheeseless/{CANARY}.txt": f"token = {CANARY}\n",
        },
    )

    assert _run(module, tmp_path, repo, _scanner(tmp_path), wheel=wheel, sdist=sdist) == 1

    out = _assert_canary_absent(capsys, tmp_path)
    assert "rule=sensitive-path" in out
    assert "rule=generic-api-key" in out


def test_ref_name_is_not_emitted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _load()
    repo = _repo(tmp_path)
    _git(repo, "branch", CANARY)

    assert _run(module, tmp_path, repo, _scanner(tmp_path)) == 1

    out = _assert_canary_absent(capsys, tmp_path)
    assert "release audit: BLOCKED" in out


def test_denylist_applies_to_annotated_tag_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    _git(repo, "tag", "-a", "v1", "-m", f"release identifier: {DENY_VALUE}")

    assert (
        _run(
            module,
            tmp_path,
            repo,
            _scanner(tmp_path),
            denylist=_operator_denylist(tmp_path),
        )
        == 1
    )

    captured = capsys.readouterr()
    assert "rule=operator-private-id" in captured.out
    assert DENY_VALUE not in captured.out and DENY_VALUE not in captured.err
    assert DENY_VALUE not in next((tmp_path / "report").glob("release-audit-*.json")).read_text()


def test_scanner_canary_in_annotated_tag_message_is_detected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    _git(repo, "tag", "-a", "v1", "-m", f"release {CANARY}")

    assert _run(module, tmp_path, repo, _scanner(tmp_path)) == 1

    out = capsys.readouterr().out
    assert "rule=generic-api-key" in out
    assert CANARY not in out


def test_deleted_historical_binary_blob_is_scanned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    (repo / "payload.bin").write_bytes(b"\x00\x01\x02" + CANARY.encode() + b"\x00")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add binary payload")
    (repo / "payload.bin").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove binary payload")
    assert CANARY.encode() not in b"".join(
        path.read_bytes() for path in repo.rglob("*") if path.is_file()
    )

    assert _run(module, tmp_path, repo, _scanner(tmp_path)) == 1

    out = capsys.readouterr().out
    assert "rule=generic-api-key" in out
    assert CANARY not in out


def test_tracked_private_state_file_is_blocked_even_when_archives_exclude_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(
        tmp_path,
        {"notes.txt": "clean\n", "seed/private.txt": "operator private state\n"},
    )
    wheel, sdist = _clean_archives(tmp_path)

    assert _run(module, tmp_path, repo, _scanner(tmp_path), wheel=wheel, sdist=sdist) == 1

    out = capsys.readouterr().out
    assert "rule=private-state" in out
    assert "seed/private.txt" not in out


def test_tar_member_cap_stops_enumeration_without_reopening(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    monkeypatch.setattr(module, "MAX_ARCHIVE_MEMBERS", 3)

    def _forbidden(*_: object, **__: object) -> list[str]:
        raise AssertionError("archive reopened after the member cap")

    monkeypatch.setattr(tarfile.TarFile, "getnames", _forbidden)
    repo = _repo(tmp_path)
    wheel = _wheel(
        tmp_path / "cap-0.0.0-py3-none-any.whl",
        {"cheeseless/__init__.py": ""},
    )
    capped = _sdist(
        tmp_path / "capped-0.0.0.tar.gz",
        {f"capped-0.0.0/file-{index}.txt": "clean\n" for index in range(10)},
    )

    assert _run(module, tmp_path, repo, _scanner(tmp_path), wheel=wheel, sdist=capped) == 2

    assert "FAILED CLOSED (archive-member-cap)" in capsys.readouterr().out


def test_denylist_inside_repository_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    denylist = repo / "denylist.toml"
    denylist.write_text("entries = []\n")

    code = _run(module, tmp_path, repo, _scanner(tmp_path), denylist=denylist)

    assert code == 2
    assert "FAILED CLOSED (denylist-inside-repository)" in capsys.readouterr().out


def test_gitleaks_config_is_a_narrow_fixture_exception() -> None:
    module = _load()
    config = module.GITLEAKS_CONFIG
    assert "useDefault = true" in config
    assert 'targetRules = ["generic-api-key"]' in config
    assert "tests/fixtures/release_audit/synthetic-token" in config
    assert "disabledRules" not in config
    assert "useDefault = false" not in config


def _real_gitleaks(tmp_path: Path) -> Path:
    destination = tmp_path / "bin"
    result = subprocess.run(  # noqa: S603 - fixed installer argv
        [
            sys.executable,
            str(ROOT / "scripts" / "install_gitleaks.py"),
            "--dest",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    scanner = destination / "gitleaks"
    if result.returncode != 0 or not scanner.is_file():
        pytest.skip("the pinned Gitleaks binary could not be obtained offline")
    return scanner


def _scan_with_real_gitleaks(scanner: Path, config: Path, target: Path, work: Path) -> int:
    report = work / f"report-{len(list(work.iterdir()))}.json"
    result = subprocess.run(  # noqa: S603 - fixed pinned scanner argv
        [
            str(scanner),
            "dir",
            ".",
            "--no-banner",
            "--redact",
            "--report-format",
            "json",
            "--report-path",
            str(report),
            "--exit-code",
            "0",
            "--config",
            str(config),
            "--log-level",
            "error",
            "--max-archive-depth",
            "0",
        ],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0
    return len(json.loads(report.read_text()))


def test_real_gitleaks_allowlist_requires_exact_path_and_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    scanner = _real_gitleaks(tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    work = tmp_path / "scan"
    work.mkdir()
    config = work / "gitleaks.toml"
    config.write_text(module.GITLEAKS_CONFIG)
    fixture_path = "tests/fixtures/release_audit/synthetic-token.txt"
    fixture = tmp_path / "synthetic-token.txt"
    fixture.write_text(f'api_key = "{module.SYNTHETIC_FIXTURE_VALUE}"\n')

    exact = tmp_path / "exact"
    (exact / fixture_path).parent.mkdir(parents=True)
    (exact / fixture_path).write_bytes(fixture.read_bytes())
    assert _scan_with_real_gitleaks(scanner, config, exact, work) == 0

    other_value = tmp_path / "other-value"
    (other_value / fixture_path).parent.mkdir(parents=True)
    alternate = "Z8g4m1Nq7Vb3Xc5" + "Ld9Rs2Ty6Ue0Ij4"
    (other_value / fixture_path).write_text(f'api_key = "{alternate}"\n')
    assert _scan_with_real_gitleaks(scanner, config, other_value, work) == 1

    other_path = tmp_path / "other-path"
    (other_path / "elsewhere").mkdir(parents=True)
    (other_path / "elsewhere" / "synthetic-token.txt").write_bytes(fixture.read_bytes())
    assert _scan_with_real_gitleaks(scanner, config, other_path, work) == 1


def test_actual_build_output_is_audited_and_packaging_is_complete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if shutil.which("uv") is None:
        pytest.skip("uv is required to build release archives")
    module = _load()
    report = tmp_path / "report"

    code = int(
        module.main(
            [
                "--repo",
                str(ROOT),
                "--gitleaks",
                str(_scanner(tmp_path)),
                "--report-dir",
                str(report),
            ]
        )
    )

    out = capsys.readouterr().out
    assert code == 0, out
    data = _report(tmp_path)
    assert data["status"] == "clean"
    assert len(data["archives"]) == 2
    assert {item["kind"] for item in data["archives"]} == {"wheel", "sdist"}
    assert not (ROOT / "dist").exists()


def test_installer_rejects_checksum_before_extract_or_execute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spec = importlib.util.spec_from_file_location(
        "install_gitleaks_test", ROOT / "scripts/install_gitleaks.py"
    )
    assert spec and spec.loader
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    calls: list[str] = []
    monkeypatch.setattr(installer, "_download", lambda _: b"invalid archive")
    monkeypatch.setattr(installer, "_extract", lambda *_: calls.append("extract"))
    monkeypatch.setattr(installer.subprocess, "run", lambda *_, **__: calls.append("execute"))
    assert installer.main(["--dest", str(tmp_path)]) == 1
    assert calls == []


def test_real_gitleaks_inline_allow_cannot_suppress_findings(tmp_path: Path) -> None:
    module = _load()
    scanner = _real_gitleaks(tmp_path)
    stage = tmp_path / "stage"
    stage.mkdir()
    token = hashlib.sha256(b"cheeseless-synthetic-canary").hexdigest()[:30]
    (stage / "config.txt").write_text(f'api_key = "{token}" # gitleaks:allow\n')
    assert module.scan_with_gitleaks(scanner, module.write_config(tmp_path), "dir", stage, tmp_path)


@pytest.mark.parametrize("overlay", ["replace", "grafts"])
def test_history_overlays_fail_closed(tmp_path: Path, overlay: str) -> None:
    module = _load()
    repo = _repo(tmp_path, {"secret.txt": DENY_VALUE})
    original = _git(repo, "rev-parse", "HEAD:secret.txt")
    _git(repo, "rm", "secret.txt")
    _git(repo, "commit", "-qm", "delete")
    if overlay == "replace":
        clean = repo / "clean.txt"
        clean.write_text("clean\n")
        replacement = _git(repo, "hash-object", "-w", str(clean))
        clean.unlink()
        _git(repo, "replace", original, replacement)
    else:
        (repo / ".git/info/grafts").write_text(_git(repo, "rev-parse", "HEAD") + "\n")
    assert (
        _run(module, tmp_path, repo, _scanner(tmp_path), denylist=_operator_denylist(tmp_path)) == 2
    )


def test_git_environment_disables_replacements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    monkeypatch.setenv("GIT_NO_REPLACE_OBJECTS", "0")
    result = module._run(
        [sys.executable, "-c", "import os; print(os.environ['GIT_NO_REPLACE_OBJECTS'])"]
    )
    assert result.stdout.strip() == b"1"


@pytest.mark.parametrize("value", [DENY_VALUE, CANARY])
def test_ref_names_are_scanned_before_redaction(
    tmp_path: Path, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    _git(repo, "branch", value)
    assert (
        _run(module, tmp_path, repo, _scanner(tmp_path), denylist=_operator_denylist(tmp_path)) == 1
    )
    assert value not in capsys.readouterr().out
    assert value not in json.dumps(_report(tmp_path))


@pytest.mark.parametrize("surface", ["zip-comment", "zip-member-comment", "pax", "gnu"])
@pytest.mark.parametrize("value", [DENY_VALUE, CANARY])
def test_archive_metadata_is_scanned(
    tmp_path: Path, surface: str, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    wheel, sdist = _clean_archives(tmp_path)
    if surface.startswith("zip"):
        with zipfile.ZipFile(wheel, "w") as handle:
            info = zipfile.ZipInfo("cheeseless/__init__.py")
            if surface == "zip-comment":
                handle.comment = value.encode()
            else:
                info.comment = value.encode()
            handle.writestr(info, "")
    else:
        fmt = tarfile.PAX_FORMAT if surface == "pax" else tarfile.GNU_FORMAT
        with tarfile.open(sdist, "w:gz", format=fmt) as handle:
            info = tarfile.TarInfo("clean.txt" if surface == "pax" else "x/" * 60 + value)
            if surface == "pax":
                info.pax_headers = {"comment": value}
            handle.addfile(info, io.BytesIO(b""))
    assert (
        _run(
            module,
            tmp_path,
            repo,
            _scanner(tmp_path),
            wheel=wheel,
            sdist=sdist,
            denylist=_operator_denylist(tmp_path),
        )
        == 1
    )
    assert value not in capsys.readouterr().out
    assert value not in json.dumps(_report(tmp_path))


def test_pax_header_budget_applies_before_tar_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    wheel, sdist = _clean_archives(tmp_path)
    with tarfile.open(sdist, "w:gz", format=tarfile.PAX_FORMAT) as handle:
        info = tarfile.TarInfo("clean.txt")
        info.pax_headers = {"comment": "x" * (1024 * 1024)}
        handle.addfile(info, io.BytesIO(b""))

    def forbidden_parser(*_: object, **__: object) -> None:
        raise AssertionError("PAX parser reached before metadata budget")

    monkeypatch.setattr(tarfile.TarInfo, "_proc_pax", forbidden_parser)
    monkeypatch.setattr(module, "MAX_ARCHIVE_MEMBER_BYTES", 32)
    assert _run(module, tmp_path, repo, _scanner(tmp_path), wheel=wheel, sdist=sdist) == 2
    assert "archive-metadata-cap" in capsys.readouterr().out


def test_zip_entry_budget_applies_before_zip_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    repo = _repo(tmp_path)
    wheel, sdist = _clean_archives(tmp_path)
    _wheel(wheel, {f"file-{i}.txt": "" for i in range(20)})

    def forbidden_parser(*_: object, **__: object) -> None:
        raise AssertionError("ZIP parser reached before entry budget")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden_parser)
    monkeypatch.setattr(module, "MAX_ARCHIVE_MEMBERS", 3)
    assert _run(module, tmp_path, repo, _scanner(tmp_path), wheel=wheel, sdist=sdist) == 2
    assert "archive-member-cap" in capsys.readouterr().out


def test_tar_extension_records_consume_shared_member_budget(tmp_path: Path) -> None:
    module = _load()
    archive = tmp_path / "metadata.tar.gz"
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as handle:
        info = tarfile.TarInfo("clean.txt")
        info.pax_headers = {"comment": "clean"}
        handle.addfile(info, io.BytesIO(b""))
    budget = module.ExtractBudget(members=2)
    module._extract_archive(archive, tmp_path / "out", budget, ())
    assert budget.members == 0


def test_archive_metadata_cannot_expand_past_scanner_byte_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 4)
    with pytest.raises(module.AuditError, match="archive-metadata-cap"):
        module._stage_archive_metadata(tmp_path / "out", b"\xff" * 3, module.ExtractBudget(), ())


def test_scanner_relative_name_does_not_execute_path_impostor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    scanner = _scanner(tmp_path)
    scanner.rename(tmp_path / "gitleaks")
    impostor_dir = tmp_path / "bin"
    impostor_dir.mkdir()
    impostor = impostor_dir / "gitleaks"
    impostor.write_text("#!/bin/sh\nexit 1\n")
    impostor.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(impostor_dir) + os.pathsep + os.environ["PATH"])
    assert module.check_scanner(Path("gitleaks"))[0] == "8.30.1"


def test_tree_ref_sensitive_path_is_checked(tmp_path: Path) -> None:
    module = _load()
    repo = _repo(tmp_path)
    (repo / ".env").write_text("clean content\n")
    _git(repo, "add", ".env")
    tree = _git(repo, "write-tree")
    _git(repo, "reset", "-q")
    (repo / ".env").unlink()
    _git(repo, "update-ref", "refs/tags/tree-only", tree)
    assert _run(module, tmp_path, repo, _scanner(tmp_path)) == 1
