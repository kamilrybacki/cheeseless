"""Redacted local release audit for cheeseLess (phase 12).

Runs the pinned Gitleaks release locally over three surfaces, without uploading
anything and without printing matched values:

* the current tracked tree,
* every locally reachable git object plus commit metadata, messages and paths,
* the sdist and wheel built from the current tree.

The only output is metadata (scanner, rule id, path/commit, count).  A full report
is written outside the repository with mode 0600.  Any finding blocks the release;
the audit never edits, deletes or rewrites repository content.

Usage::

    uv run python scripts/release_audit.py --report-dir /tmp/cheeseless-audit

    uv run python scripts/release_audit.py --release --remote-refs-attested \
        --denylist /path/outside/repo/denylist.toml --report-dir /tmp/cheeseless-audit

Install the pinned scanner first with ``scripts/install_gitleaks.py``.  A missing,
unexpectedly versioned or failing scanner is a fail-closed infrastructure error.  The
scanner binary is hashed against the pinned Linux/x86_64 digest: default mode records a
mismatch as a limitation, while ``--release`` fails closed.  ``--release`` also requires a
non-empty operator denylist and blocks on ``remote-refs-unattested`` until the operator
attests that server-side refs no longer expose pre-rewrite commits.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess  # noqa: S404 - the scanner is a process boundary
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

GITLEAKS_VERSION = "8.30.1"
GITLEAKS_LINUX_X64_URL = (
    "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/"
    "gitleaks_8.30.1_linux_x64.tar.gz"
)
GITLEAKS_LINUX_X64_SHA256 = "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
GITLEAKS_BINARY_SHA256: dict[tuple[str, str], str] = {
    ("Linux", "x86_64"): "88f91962aa2f93ac6ab281d553b9e125f5197bbbce38f9f2437f7299c32e5509",
}

SCHEMA = "cheeseless.release-audit.v1"
GITLEAKS_TIMEOUT_SECONDS = 900
GIT_TIMEOUT_SECONDS = 120

MAX_TREE_FILES = 20_000
MAX_TREE_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_HISTORY_OBJECTS = 500_000
MAX_HISTORY_BLOB_BYTES = 50 * 1024 * 1024
MAX_HISTORY_STAGE_BYTES = 512 * 1024 * 1024
MAX_HISTORY_METADATA_BYTES = 32 * 1024 * 1024
MAX_METADATA_FILE_BYTES = 32 * 1024 * 1024
MAX_REFS = 10_000
MAX_ARCHIVES = 8
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_MEMBER_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 256 * 1024 * 1024
MAX_NESTED_DEPTH = 2
MAX_MEMBER_PATH = 512
MAX_DENYLIST_BYTES = 64 * 1024
MAX_DENYLIST_ENTRIES = 64
MAX_DENYLIST_PATTERN = 500
MAX_FINDINGS = 2_000
MAX_REPORT_BYTES = 8 * 1024 * 1024

ARCHIVE_SUFFIXES = (".whl", ".zip", ".tar.gz", ".tgz", ".tar")

SYNTHETIC_FIXTURE_PATH = "tests/fixtures/release_audit/synthetic-token.txt"
SYNTHETIC_FIXTURE_VALUE = "Q7f3k9Lm2Pz8Vx4Nc6Rt1Wy5Bd0Hg2"

SENSITIVE_COMPONENTS = frozenset(
    {
        ".env",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        "auth.json",
        "credentials",
        "credentials.json",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "known_hosts",
    }
)
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
PRIVATE_STATE_COMPONENTS = frozenset(
    {
        ".cheeseless",
        ".import_linter_cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "evidence",
        "graft",
        "seed",
    }
)
PRIVATE_STATE_SUFFIXES = (".pyc", ".pyo")
PACKAGE_FORBIDDEN_COMPONENTS = frozenset({"acceptance"})

GITLEAKS_CONFIG = f"""\
title = "cheeseless release audit (default rules, one narrow fixture exception)"

[extend]
useDefault = true

# The repository ships one synthetic credential-shaped fixture so the release
# audit has a known positive control.  Only generic-api-key is exempted, and only
# when the exact fixture value appears at the exact repository-root path.  Both
# conditions are required together; high-signal rules and every other path stay
# enabled.
[[allowlists]]
description = "Synthetic fixture only (tests/fixtures/release_audit/synthetic-token.txt)."
targetRules = ["generic-api-key"]
paths = ['''^{re.escape(SYNTHETIC_FIXTURE_PATH)}$''']
regexTarget = "secret"
regexes = ['''^{re.escape(SYNTHETIC_FIXTURE_VALUE)}$''']
condition = "AND"
"""


class AuditError(Exception):
    """A fail-closed infrastructure condition; the message is a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Finding:
    scanner: str
    rule: str
    location: str
    commit: str | None = None
    line: int | None = None
    count: int = 1


@dataclass(frozen=True)
class ArchiveInfo:
    kind: str
    name: str
    sha256: str


@dataclass(frozen=True)
class DenyRule:
    rule_id: str
    pattern: re.Pattern[str]


@dataclass
class ExtractBudget:
    members: int = field(default_factory=lambda: MAX_ARCHIVE_MEMBERS)
    total_bytes: int = field(default_factory=lambda: MAX_ARCHIVE_TOTAL_BYTES)
    names: set[str] = field(default_factory=set)
    exhausted: bool = False
    findings: list[Finding] = field(default_factory=list)


@dataclass(frozen=True)
class AuditResult:
    status: str
    scanner_version: str
    scanner_sha256: str
    refs: tuple[str, ...]
    archives: tuple[ArchiveInfo, ...]
    findings: tuple[Finding, ...]
    blocked: tuple[str, ...]
    limitations: tuple[str, ...]
    release_mode: bool = False
    remote_refs_attested: bool = False


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
    timeout: int = GIT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, trusted local tools
            argv,
            cwd=cwd,
            env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuditError("command-failed") from exc


def _git(repo: Path, *args: str) -> bytes:
    result = _run(["git", "-C", str(repo), *args])
    if result.returncode != 0:
        raise AuditError("git-failed")
    return result.stdout


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_relative(name: str) -> bool:
    if not name or len(name) > MAX_MEMBER_PATH or name.startswith("/"):
        return False
    if "\\" in name:
        return False
    parts = name.split("/")
    return all(part not in ("", ".", "..") for part in parts)


def _sensitive_reason(name: str) -> str | None:
    parts = name.split("/")
    for part in parts:
        lowered = part.lower()
        if lowered in SENSITIVE_COMPONENTS or lowered.startswith(".env."):
            return "sensitive-path"
        if lowered.endswith(SENSITIVE_SUFFIXES):
            return "sensitive-path"
    return None


def _private_state_reason(name: str) -> str | None:
    parts = name.split("/")
    for part in parts:
        if part in PRIVATE_STATE_COMPONENTS or part.endswith(PRIVATE_STATE_SUFFIXES):
            return "private-state"
    return None


def _archive_policy_reason(name: str) -> str | None:
    sensitive = _sensitive_reason(name)
    if sensitive is not None:
        return sensitive
    private = _private_state_reason(name)
    if private is not None:
        return private
    if any(part in PACKAGE_FORBIDDEN_COMPONENTS for part in name.split("/")):
        return "acceptance-hidden"
    return None


def _sanitize_location(location: str, match: str | None = None) -> str:
    if match:
        location = location.replace(match, "<redacted>")
    return location


def _opaque_location(category: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]
    return f"{category}:{digest}"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise AuditError("file-unreadable") from exc
    return digest.hexdigest()


def check_scanner(path: Path) -> tuple[str, str]:
    """Return ``(version, sha256)`` or fail closed without echoing scanner text."""
    path = path.resolve()
    if not path.exists() or not path.is_file() or not os.access(path, os.X_OK):
        raise AuditError("gitleaks-missing")
    digest = _hash_file(path)
    result = _run([str(path), "version"], timeout=30)
    if result.returncode != 0:
        raise AuditError("gitleaks-incompatible")
    version = result.stdout.decode("utf-8", "replace").strip()
    if version != GITLEAKS_VERSION:
        raise AuditError("gitleaks-incompatible")
    return version, digest


def _scanner_digest_problem(digest: str) -> str | None:
    system = platform.system()
    machine = platform.machine()
    expected = GITLEAKS_BINARY_SHA256.get((system, machine))
    if expected is None:
        return (
            "The scanner binary SHA-256 is pinned only for Linux/x86_64; this host is "
            f"{system}/{machine}, so the scanner binary is unverified."
        )
    if digest != expected:
        return (
            "The scanner binary SHA-256 does not match the pinned Linux/x86_64 digest, "
            "so the scanner binary is unverified."
        )
    return None


def load_denylist(path: Path | None, repo: Path) -> tuple[DenyRule, ...]:
    if path is None:
        return ()
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise AuditError("denylist-unreadable")
    if _within(resolved, repo.resolve()):
        raise AuditError("denylist-inside-repository")
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise AuditError("denylist-unreadable") from exc
    if len(raw) > MAX_DENYLIST_BYTES:
        raise AuditError("denylist-too-large")
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise AuditError("denylist-invalid") from exc
    entries = parsed.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_DENYLIST_ENTRIES:
        raise AuditError("denylist-invalid")
    rules: list[DenyRule] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise AuditError("denylist-invalid")
        rule_id = entry.get("id")
        pattern = entry.get("pattern")
        if not isinstance(rule_id, str) or not isinstance(pattern, str):
            raise AuditError("denylist-invalid")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", rule_id):
            raise AuditError("denylist-invalid")
        if not pattern or len(pattern) > MAX_DENYLIST_PATTERN:
            raise AuditError("denylist-invalid")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise AuditError("denylist-invalid") from exc
        rules.append(DenyRule(rule_id, compiled))
    return tuple(rules)


def _denylist_text(
    rules: tuple[DenyRule, ...],
    text: str,
    location: str,
    scanner: str = "denylist",
) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for rule in rules:
            match = rule.pattern.search(line)
            if match is not None:
                findings.append(
                    Finding(
                        scanner=scanner,
                        rule=rule.rule_id,
                        location=_sanitize_location(location, match.group(0)),
                        line=number,
                    )
                )
    return findings


def _denylist_file(rules: tuple[DenyRule, ...], path: Path, location: str) -> list[Finding]:
    if not rules:
        return []
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise AuditError("file-unreadable") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise AuditError("file-too-large")
    return _denylist_text(rules, raw.decode("utf-8", "replace"), location)


def _history_object_contents(
    repo: Path, objects: list[tuple[bytes, str, int]]
) -> Iterator[tuple[bytes, str, bytes]]:
    """Yield bounded raw content for reachable blobs and annotated tags."""
    if not objects:
        return
    process = subprocess.Popen(  # noqa: S603 - fixed git argv, trusted local tool
        ["git", "-C", str(repo), "cat-file", "--batch"],  # noqa: S607
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    stdin = process.stdin
    stdout = process.stdout
    if stdin is None or stdout is None:
        process.kill()
        raise AuditError("history-incomplete")
    try:
        for oid, kind, expected in objects:
            stdin.write(oid + b"\n")
            stdin.flush()
            header = stdout.readline()
            if not header:
                raise AuditError("history-incomplete")
            fields = header.split()
            if len(fields) != 3 or not fields[2].isdigit():
                raise AuditError("history-incomplete")
            if fields[1].decode("ascii", "replace") != kind:
                raise AuditError("history-incomplete")
            size = int(fields[2])
            if size != expected:
                raise AuditError("history-incomplete")
            body = stdout.read(size)
            if len(body) != size:
                raise AuditError("history-incomplete")
            if stdout.read(1) != b"\n":
                raise AuditError("history-incomplete")
            yield oid, kind, body
    except OSError as exc:
        raise AuditError("history-incomplete") from exc
    finally:
        stdin.close()
        try:
            process.wait(timeout=GIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            raise AuditError("command-failed") from exc
    if process.returncode != 0:
        raise AuditError("history-incomplete")


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise AuditError("private-file-failed") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except OSError as exc:
        raise AuditError("private-file-failed") from exc


def stage_tracked_tree(
    repo: Path, stage: Path, rules: tuple[DenyRule, ...]
) -> tuple[list[Finding], int]:
    """Copy the current tracked tree into ``stage`` without reading sensitive files."""
    raw = _git(repo, "ls-files", "-z")
    try:
        names = [item.decode("utf-8", "strict") for item in raw.split(b"\0") if item]
    except UnicodeDecodeError as exc:
        raise AuditError("path-encoding") from exc
    if len(names) > MAX_TREE_FILES:
        raise AuditError("tree-cap")
    findings: list[Finding] = []
    total = 0
    for name in names:
        if not _safe_relative(name):
            findings.append(Finding("hygiene", "unsafe-tracked-path", "<unsafe-path>"))
            continue
        location = _opaque_location("tracked-file", name)
        reason = _sensitive_reason(name) or _private_state_reason(name)
        if reason is not None:
            findings.append(Finding("hygiene", reason, location))
            continue
        source = repo / name
        try:
            info = source.lstat()
        except OSError:
            findings.append(Finding("hygiene", "tracked-file-missing", location))
            continue
        destination = stage / name
        if stat.S_ISLNK(info.st_mode):
            try:
                content = f"symlink target: {os.readlink(source)}".encode()
            except OSError:
                findings.append(Finding("hygiene", "tracked-symlink-unreadable", location))
                continue
        elif stat.S_ISREG(info.st_mode):
            if info.st_size > MAX_FILE_BYTES:
                findings.append(Finding("hygiene", "tracked-file-too-large", location))
                continue
            try:
                content = source.read_bytes()
            except OSError as exc:
                raise AuditError("file-unreadable") from exc
        else:
            findings.append(Finding("hygiene", "tracked-non-regular", location))
            continue
        total += len(content)
        if total > MAX_TREE_BYTES:
            raise AuditError("tree-cap")
        findings.extend(_denylist_text(rules, content.decode("utf-8", "replace"), location))
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            os.chmod(destination, 0o600)
        except OSError as exc:
            raise AuditError("stage-failed") from exc
    return findings, total


def _history_path_names(repo: Path) -> set[str]:
    """Enumerate every distinct historical path occurrence.

    ``git rev-list --objects`` reports each object id once, so identical blob
    content committed under both a clean and a sensitive path would surface only
    one of the names.  This walks every commit's changed paths instead, with
    rename detection disabled so both sides of a rename are listed.
    """
    raw = _git(
        repo,
        "log",
        "--all",
        "--root",
        "-m",
        "--no-renames",
        "--name-only",
        "--format=",
        "-z",
    )
    if len(raw) > MAX_METADATA_FILE_BYTES:
        raise AuditError("history-path-cap")
    names: set[str] = set()
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            names.add(item.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise AuditError("history-path-invalid") from exc
    return names


def history_metadata(repo: Path, stage: Path, rules: tuple[DenyRule, ...]) -> list[Finding]:
    """Write commit metadata/messages and historical paths to the scan stage."""
    raw = _git(
        repo,
        "log",
        "--all",
        "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e",
    )
    if len(raw) > MAX_HISTORY_METADATA_BYTES:
        raise AuditError("history-metadata-cap")
    findings: list[Finding] = []
    records: list[str] = []
    for record in raw.split(b"\x1e"):
        record = record.strip(b"\n")
        if not record:
            continue
        fields = record.split(b"\x1f")
        if len(fields) != 6:
            raise AuditError("history-metadata-invalid")
        sha, author, email, committer, committer_email, body = (
            field.decode("utf-8", "replace") for field in fields
        )
        if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
            raise AuditError("history-metadata-invalid")
        text = (
            f"commit: {sha}\n"
            f"author: {author} <{email}>\n"
            f"committer: {committer} <{committer_email}>\n"
            f"message:\n{body}\n"
        )
        records.append(text)
        findings.extend(_denylist_text(rules, text, f"history:commit {sha}"))
    if not records:
        raise AuditError("history-empty")
    metadata = "\n".join(records).encode()
    _write_private(stage / "release-audit-history-metadata.txt", metadata)

    objects = _git(repo, "rev-list", "--objects", "--all")
    if len(objects) > MAX_HISTORY_OBJECTS * 80:
        raise AuditError("history-object-cap")
    object_ids: list[bytes] = []
    object_paths: dict[bytes, str] = {}
    for line in objects.splitlines():
        if not line:
            continue
        parts = line.split(b" ", 1)
        if not re.fullmatch(rb"[0-9a-f]{40,64}", parts[0]):
            raise AuditError("history-path-invalid")
        object_ids.append(parts[0])
        if len(parts) == 2:
            try:
                object_paths.setdefault(parts[0], parts[1].decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise AuditError("history-path-invalid") from exc
    if len(object_ids) > MAX_HISTORY_OBJECTS:
        raise AuditError("history-object-cap")
    check = _run(
        ["git", "-C", str(repo), "cat-file", "--batch-check=%(objecttype) %(objectsize)"],
        input_bytes=b"\n".join(object_ids) + b"\n",
    )
    if check.returncode != 0:
        raise AuditError("history-incomplete")
    lines = check.stdout.splitlines()
    if len(lines) != len(object_ids):
        raise AuditError("history-incomplete")
    raw_objects: list[tuple[bytes, str, int]] = []
    for oid, line in zip(object_ids, lines, strict=True):
        parts = line.split()
        if len(parts) != 2 or not parts[1].isdigit():
            raise AuditError("history-incomplete")
        kind = parts[0].decode("ascii", "replace")
        size = int(parts[1])
        if kind in ("blob", "tag"):
            if size > MAX_HISTORY_BLOB_BYTES:
                label = "history-blob-too-large" if kind == "blob" else "history-tag-too-large"
                findings.append(Finding("hygiene", label, f"history-{kind}:{oid.decode()}"))
            else:
                raw_objects.append((oid, kind, size))
    fixture_bytes: bytes | None = None
    fixture_source = repo / SYNTHETIC_FIXTURE_PATH
    try:
        if stat.S_ISREG(fixture_source.lstat().st_mode):
            fixture_bytes = fixture_source.read_bytes()
    except OSError:
        fixture_bytes = None
    history_root = stage / "history-objects"
    staged_bytes = 0
    for oid, kind, content in _history_object_contents(repo, raw_objects):
        location = f"history-{kind}:{oid.decode()}"
        text = content.decode("utf-8", "replace")
        findings.extend(_denylist_text(rules, text, location))
        path = object_paths.get(oid)
        if (
            kind == "blob"
            and path == SYNTHETIC_FIXTURE_PATH
            and fixture_bytes is not None
            and content == fixture_bytes
        ):
            continue
        staged_bytes += len(content)
        if staged_bytes > MAX_HISTORY_STAGE_BYTES:
            raise AuditError("history-stage-cap")
        _write_private(history_root / oid.decode(), content)
    historical_names = _history_path_names(repo) | set(object_paths.values())
    for name in historical_names:
        location = _opaque_location("history-path", name)
        reason = _sensitive_reason(name)
        if reason is not None:
            findings.append(Finding("hygiene", f"history-{reason}", location))
        findings.extend(_denylist_text(rules, name, location))
    path_text = "\n".join(sorted(historical_names)) + "\n"
    if len(path_text.encode()) > MAX_METADATA_FILE_BYTES:
        raise AuditError("history-path-cap")
    _write_private(stage / "release-audit-history-paths.txt", path_text.encode())
    return findings


def write_config(work: Path) -> Path:
    path = work / "gitleaks.toml"
    _write_private(path, GITLEAKS_CONFIG.encode())
    return path


def _parse_report(path: Path) -> list[Finding]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise AuditError("gitleaks-report-missing") from exc
    if size > MAX_REPORT_BYTES:
        raise AuditError("gitleaks-report-too-large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditError("gitleaks-report-invalid") from exc
    if not isinstance(payload, list):
        raise AuditError("gitleaks-report-invalid")
    findings: list[Finding] = []
    for item in payload:
        if not isinstance(item, dict):
            raise AuditError("gitleaks-report-invalid")
        rule = item.get("RuleID")
        file = item.get("File")
        if not isinstance(rule, str) or not rule or not isinstance(file, str) or not file:
            raise AuditError("gitleaks-report-invalid")
        commit = item.get("Commit")
        line = item.get("StartLine")
        findings.append(
            Finding(
                scanner="gitleaks",
                rule=rule,
                location=_opaque_location("scanner-file", file),
                commit=commit if isinstance(commit, str) and commit else None,
                line=line if isinstance(line, int) and line > 0 else None,
            )
        )
    return findings


def scan_with_gitleaks(
    scanner: Path, config: Path, mode: str, target: Path, work: Path
) -> list[Finding]:
    report = work / f"gitleaks-{mode}-{os.getpid()}-{id(target)}.json"
    target_argument = "." if mode == "dir" else str(target)
    command = [
        str(scanner),
        mode,
        target_argument,
        "--no-banner",
        "--redact",
        "--ignore-gitleaks-allow",
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
        "--max-target-megabytes",
        str(MAX_FILE_BYTES // (1024 * 1024)),
        "--max-archive-depth",
        "0",
    ]
    if mode == "git":
        command.append("--log-opts=--all")
    try:
        result = _run(
            command,
            cwd=target if mode == "dir" else None,
            timeout=GITLEAKS_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise AuditError("gitleaks-scan-failed")
        return _parse_report(report)
    finally:
        report.unlink(missing_ok=True)


def _validate_member_name(name: str, index: int) -> tuple[str, str | None]:
    if not _safe_relative(name):
        return f"archive-member-{index}", "archive-unsafe-member"
    return name, None


def _copy_bounded(source: IO[bytes], destination: Path, limit: int, budget: ExtractBudget) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(destination, flags, 0o600)
    except OSError:
        return False
    written = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > limit or written > budget.total_bytes:
                    destination.unlink(missing_ok=True)
                    return False
                handle.write(chunk)
    except OSError:
        destination.unlink(missing_ok=True)
        return False
    budget.total_bytes -= written
    return True


def _consume_member(budget: ExtractBudget) -> bool:
    if budget.members <= 0:
        return False
    budget.members -= 1
    return True


def _stage_archive_metadata(
    root: Path, data: bytes, budget: ExtractBudget, rules: tuple[DenyRule, ...]
) -> None:
    if len(data) > MAX_ARCHIVE_MEMBER_BYTES or len(data) > budget.total_bytes:
        raise AuditError("archive-metadata-cap")
    text = data.decode("utf-8", "replace").replace("\0", "\n")
    encoded = text.encode()
    if len(encoded) > min(MAX_FILE_BYTES, budget.total_bytes):
        raise AuditError("archive-metadata-cap")
    budget.total_bytes -= len(encoded)
    budget.findings.extend(_denylist_text(rules, text, "archive:metadata"))
    root.mkdir(parents=True, exist_ok=True)
    metadata_root = Path(tempfile.mkdtemp(prefix="audit-metadata-", dir=root))
    _write_private(metadata_root / "metadata.txt", encoded)


def _preflight_tar(
    archive: Path, root: Path, budget: ExtractBudget, rules: tuple[DenyRule, ...]
) -> None:
    """Walk physical records before tarfile can interpret extension headers."""
    with archive.open("rb") as probe:
        compressed = probe.read(2) == b"\x1f\x8b"
    opener = gzip.open if compressed else Path.open
    remaining = budget.total_bytes + 512 * (budget.members + 2)
    count = 0
    extensions = 0
    metadata = bytearray()
    with opener(archive, "rb") as stream:
        while True:
            header = stream.read(512)
            remaining -= len(header)
            if not header or header == b"\0" * 512:
                break
            if len(header) != 512 or remaining < 0:
                raise AuditError("archive-expansion-cap")
            count += 1
            if count > budget.members:
                raise AuditError("archive-member-cap")
            member = tarfile.TarInfo.frombuf(header, "utf-8", "replace")
            if member.size < 0 or member.type == tarfile.GNUTYPE_SPARSE:
                raise AuditError("archive-unsupported")
            extension = member.type in (
                tarfile.XHDTYPE,
                tarfile.XGLTYPE,
                tarfile.GNUTYPE_LONGNAME,
                tarfile.GNUTYPE_LONGLINK,
            )
            extensions += int(extension)
            size = member.size
            if extension and (size > MAX_ARCHIVE_MEMBER_BYTES or size > budget.total_bytes):
                raise AuditError("archive-metadata-cap")
            padded = (size + 511) // 512 * 512
            if padded > remaining:
                raise AuditError("archive-expansion-cap")
            metadata_size = len(metadata) + 512 + (padded if extension else 0)
            if metadata_size > min(MAX_ARCHIVE_MEMBER_BYTES, budget.total_bytes):
                raise AuditError("archive-metadata-cap")
            metadata.extend(header)
            while padded:
                chunk = stream.read(min(padded, 64 * 1024))
                if not chunk:
                    raise AuditError("archive-unreadable")
                if extension:
                    metadata.extend(chunk)
                padded -= len(chunk)
                remaining -= len(chunk)
                if len(metadata) > min(MAX_ARCHIVE_TOTAL_BYTES, budget.total_bytes):
                    raise AuditError("archive-metadata-cap")
    budget.members -= extensions
    _stage_archive_metadata(root, bytes(metadata), budget, rules)


def _preflight_zip(archive: Path, budget: ExtractBudget) -> None:
    """Validate bounded central-directory records before ZipFile allocates entries.

    ZIP64 and split archives are intentionally refused.
    """
    with archive.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        length = stream.tell()
        stream.seek(max(0, length - 65557))
        tail = stream.read(65557)
        offset = tail.rfind(b"PK\x05\x06")
        if offset < 0 or len(tail) - offset < 22:
            raise AuditError("archive-unreadable")
        end = tail[offset:]
        count = int.from_bytes(end[10:12], "little")
        size = int.from_bytes(end[12:16], "little")
        start = int.from_bytes(end[16:20], "little")
        comment_size = int.from_bytes(end[20:22], "little")
        if (
            end[4:8] != b"\0" * 4
            or end[8:10] != end[10:12]
            or count == 65535
            or size == 0xFFFFFFFF
            or start == 0xFFFFFFFF
        ):
            raise AuditError("archive-unsupported")
        if count > budget.members:
            raise AuditError("archive-member-cap")
        if size + comment_size > min(MAX_ARCHIVE_TOTAL_BYTES, budget.total_bytes):
            raise AuditError("archive-metadata-cap")
        if len(end) != 22 + comment_size or start + size != length - len(end):
            raise AuditError("archive-unreadable")
        stream.seek(start)
        consumed = 0
        for _ in range(count):
            fixed = stream.read(46)
            if len(fixed) != 46 or fixed[:4] != b"PK\x01\x02":
                raise AuditError("archive-unreadable")
            variable = sum(int.from_bytes(fixed[i : i + 2], "little") for i in (28, 30, 32))
            consumed += 46 + variable
            if consumed > size:
                raise AuditError("archive-unreadable")
            stream.seek(variable, os.SEEK_CUR)
        if consumed != size:
            raise AuditError("archive-unreadable")


def _extract_tar(
    archive: Path, root: Path, budget: ExtractBudget, rules: tuple[DenyRule, ...]
) -> None:
    try:
        _preflight_tar(archive, root, budget, rules)
        with tarfile.open(archive, "r:*") as handle:
            seen: set[str] = set()
            for index, member in enumerate(handle):
                _stage_archive_metadata(
                    root,
                    (member.name + "\n" + json.dumps(member.pax_headers)).encode(),
                    budget,
                    rules,
                )
                if not _consume_member(budget):
                    budget.findings.append(
                        Finding("hygiene", "archive-member-cap", f"archive:#{index}")
                    )
                    budget.exhausted = True
                    return
                name, unsafe = _validate_member_name(member.name, index)
                if unsafe is not None:
                    budget.findings.append(Finding("hygiene", unsafe, name))
                    continue
                location = _opaque_location("archive-member", name)
                if name in seen:
                    budget.findings.append(Finding("hygiene", "archive-duplicate-member", location))
                    continue
                seen.add(name)
                budget.names.add(name)
                policy = _archive_policy_reason(name)
                if policy is not None:
                    budget.findings.append(Finding("hygiene", policy, location))
                    continue
                budget.findings.extend(_denylist_text(rules, name, location))
                if member.isdir():
                    continue
                if not member.isfile():
                    budget.findings.append(Finding("hygiene", "archive-unsafe-link", location))
                    continue
                if member.size > MAX_ARCHIVE_MEMBER_BYTES:
                    budget.findings.append(Finding("hygiene", "archive-member-too-large", location))
                    continue
                source = handle.extractfile(member)
                if source is None:
                    budget.findings.append(
                        Finding("hygiene", "archive-member-unreadable", location)
                    )
                    continue
                if not _copy_bounded(source, root / name, member.size, budget):
                    budget.findings.append(Finding("hygiene", "archive-expansion-cap", location))
                    continue
                budget.findings.extend(_denylist_file(rules, root / name, location))
    except (tarfile.TarError, OSError) as exc:
        raise AuditError("archive-unreadable") from exc


def _extract_zip(
    archive: Path, root: Path, budget: ExtractBudget, rules: tuple[DenyRule, ...]
) -> None:
    try:
        _preflight_zip(archive, budget)
        with zipfile.ZipFile(archive) as handle:
            _stage_archive_metadata(root, handle.comment, budget, rules)
            seen: set[str] = set()
            for index, info in enumerate(handle.infolist()):
                if not _consume_member(budget):
                    budget.findings.append(
                        Finding("hygiene", "archive-member-cap", f"archive:#{index}")
                    )
                    budget.exhausted = True
                    return
                _stage_archive_metadata(root, info.comment + b"\n" + info.extra, budget, rules)
                name, unsafe = _validate_member_name(info.filename, index)
                if unsafe is not None:
                    budget.findings.append(Finding("hygiene", unsafe, name))
                    continue
                location = _opaque_location("archive-member", name)
                if name in seen:
                    budget.findings.append(Finding("hygiene", "archive-duplicate-member", location))
                    continue
                seen.add(name)
                budget.names.add(name)
                policy = _archive_policy_reason(name)
                if policy is not None:
                    budget.findings.append(Finding("hygiene", policy, location))
                    continue
                budget.findings.extend(_denylist_text(rules, name, location))
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    budget.findings.append(Finding("hygiene", "archive-unsafe-link", location))
                    continue
                if info.flag_bits & 0x1:
                    budget.findings.append(Finding("hygiene", "archive-encrypted-member", location))
                    continue
                if info.is_dir():
                    continue
                if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    budget.findings.append(Finding("hygiene", "archive-member-too-large", location))
                    continue
                source = handle.open(info)
                if not _copy_bounded(source, root / name, info.file_size, budget):
                    budget.findings.append(Finding("hygiene", "archive-expansion-cap", location))
                    continue
                budget.findings.extend(_denylist_file(rules, root / name, location))
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise AuditError("archive-unreadable") from exc


def _extract_archive(
    archive: Path, root: Path, budget: ExtractBudget, rules: tuple[DenyRule, ...]
) -> None:
    try:
        info = archive.stat()
    except OSError as exc:
        raise AuditError("archive-missing") from exc
    if not stat.S_ISREG(info.st_mode):
        raise AuditError("archive-missing")
    if info.st_size > MAX_ARCHIVE_BYTES:
        raise AuditError("archive-too-large")
    if zipfile.is_zipfile(archive):
        _extract_zip(archive, root, budget, rules)
        return
    if archive.name.lower().endswith((".tar", ".tar.gz", ".tgz")):
        _extract_tar(archive, root, budget, rules)
        return
    raise AuditError("archive-unsupported")


def _nested_archives(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name.lower().endswith(ARCHIVE_SUFFIXES)
    )


def _expected_modules(repo: Path) -> set[str]:
    source = repo / "src" / "cheeseless"
    if not source.is_dir():
        return set()
    return {path.relative_to(source).as_posix() for path in source.rglob("*.py") if path.is_file()}


def _module_findings(kind: str, names: set[str], expected: set[str]) -> list[Finding]:
    if not expected:
        return []
    findings: list[Finding] = []
    if kind == "wheel":
        present = names
    else:
        prefixes = {name.split("/", 1)[0] for name in names if "/" in name}
        prefix = next(iter(prefixes)) if len(prefixes) == 1 else ""
        if not prefix:
            return [Finding("hygiene", "archive-missing-module", "<source-root>")]
        present = {
            name[len(prefix) + len("/src/") :]
            for name in names
            if name.startswith(f"{prefix}/src/")
        }
    for module in sorted(expected):
        target = f"cheeseless/{module}"
        if target not in present:
            findings.append(
                Finding(
                    "hygiene",
                    "archive-missing-module",
                    _opaque_location("module", target),
                )
            )
    return findings


def inspect_archive(
    archive: Path,
    kind: str,
    work: Path,
    scanner: Path,
    config: Path,
    rules: tuple[DenyRule, ...],
    expected: set[str],
) -> tuple[list[Finding], ArchiveInfo]:
    digest = _hash_file(archive)
    budget = ExtractBudget()
    root = work / f"archive-{kind}-{len(list(work.iterdir()))}"
    root.mkdir(mode=0o700)
    findings: list[Finding] = []
    try:
        _extract_archive(archive, root, budget, rules)
        findings.extend(budget.findings)
        findings.extend(_module_findings(kind, budget.names, expected))
        depth = 0
        pending = [] if budget.exhausted else _nested_archives(root)
        while pending:
            if depth >= MAX_NESTED_DEPTH:
                findings.extend(
                    Finding("hygiene", "archive-depth-cap", f"archive:#{index}")
                    for index, _ in enumerate(pending)
                )
                break
            next_round: list[Path] = []
            for index, nested in enumerate(pending):
                nested_root = root / f"__nested-{depth}-{index}"
                nested_root.mkdir(mode=0o700)
                nested_budget = ExtractBudget(
                    members=budget.members, total_bytes=budget.total_bytes
                )
                _extract_archive(nested, nested_root, nested_budget, rules)
                findings.extend(nested_budget.findings)
                budget.members = nested_budget.members
                budget.total_bytes = nested_budget.total_bytes
                nested.unlink(missing_ok=True)
                if not nested_budget.exhausted:
                    next_round.extend(_nested_archives(nested_root))
            pending = next_round
            depth += 1
        findings.extend(scan_with_gitleaks(scanner, config, "dir", root, work))
        return findings, ArchiveInfo(kind, _opaque_location("archive", archive.name), digest)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _build_archives(repo: Path, destination: Path) -> list[Path]:
    destination.mkdir(mode=0o700, parents=True)
    result = _run(
        ["uv", "build", "--sdist", "--wheel", "--out-dir", str(destination)],
        cwd=repo,
        timeout=300,
    )
    if result.returncode != 0:
        raise AuditError("build-failed")
    wheels = sorted(destination.glob("*.whl"))
    sdists = sorted(destination.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise AuditError("build-output-incomplete")
    return [wheels[0], sdists[0]]


def _provided_archives(wheels: list[Path], sdists: list[Path]) -> list[Path]:
    if len(wheels) + len(sdists) > MAX_ARCHIVES:
        raise AuditError("archive-cap")
    paths = [*wheels, *sdists]
    for path in paths:
        if not path.is_file():
            raise AuditError("archive-missing")
    return paths


def _limitations(extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    return (
        "Only local refs are audited. Server-side pull refs and remote-only history "
        "cannot be enumerated from this clone.",
        "GitHub refs/pull/1 and refs/pull/2 are known to reference pre-rewrite commits "
        "that still carry the old author email. This audit cannot inspect or remove them; "
        "GitHub Support or a fresh repository is required. Do not claim history is sanitized.",
        f"Gitleaks {GITLEAKS_VERSION} detection is heuristic, not proof of absence.",
        "An optional operator denylist is trusted input and must stay outside the repository.",
        *extra,
    )


def _aggregate(findings: list[Finding]) -> tuple[Finding, ...]:
    counts: Counter[tuple[str, str, str, str | None, int | None]] = Counter(
        (item.scanner, item.rule, item.location, item.commit, item.line) for item in findings
    )
    aggregated = [
        Finding(scanner, rule, location, commit, line, count)
        for (scanner, rule, location, commit, line), count in counts.items()
    ]
    aggregated.sort(key=lambda item: (item.scanner, item.rule, item.location, item.commit or ""))
    return tuple(aggregated[:MAX_FINDINGS])


def _report_path(report_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return report_dir / f"release-audit-{stamp}-{os.getpid()}.json"


def write_report(report_dir: Path, repo: Path, result: AuditResult) -> Path:
    resolved = report_dir.expanduser().resolve()
    if _within(resolved, repo.resolve()):
        raise AuditError("report-inside-repository")
    if resolved.exists() and resolved.is_symlink():
        raise AuditError("report-dir-unsafe")
    try:
        resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(resolved, 0o700)
    except OSError as exc:
        raise AuditError("report-dir-unwritable") from exc
    path = _report_path(resolved)
    payload = {
        "schema": SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": result.status,
        "scanner": {
            "name": "gitleaks",
            "version": result.scanner_version,
            "sha256": result.scanner_sha256,
        },
        "local_refs": list(result.refs),
        "archives": [
            {"kind": item.kind, "name": item.name, "sha256": item.sha256}
            for item in result.archives
        ],
        "findings": [
            {
                "scanner": item.scanner,
                "rule": item.rule,
                "location": item.location,
                "commit": item.commit,
                "line": item.line,
                "count": item.count,
            }
            for item in result.findings
        ],
        "blocked": list(result.blocked),
        "limitations": list(result.limitations),
        "release_mode": result.release_mode,
        "remote_refs_attested": result.remote_refs_attested,
    }
    _write_private(path, json.dumps(payload, indent=2, sort_keys=True).encode())
    return path


def _print_summary(result: AuditResult, report_written: bool) -> None:
    print(f"release audit: {result.status.upper()}")
    print(f"scanner: gitleaks {result.scanner_version}")
    print(f"local refs: {len(result.refs)}")
    print(f"archives: {len(result.archives)}")
    for item in result.findings:
        commit = f" commit={item.commit}" if item.commit else ""
        line = f" line={item.line}" if item.line else ""
        print(
            f"finding: scanner={item.scanner} rule={item.rule} "
            f"path={item.location}{commit}{line} count={item.count}"
        )
    for reason in result.blocked:
        print(f"blocked: {reason}")
    for note in result.limitations:
        print(f"limitation: {note}")
    print(
        "report: private metadata-only report written outside the repository"
        if report_written
        else "report: not written"
    )


def audit(
    *,
    repo: Path,
    scanner_path: Path,
    report_dir: Path,
    denylist: Path | None = None,
    wheels: list[Path] | None = None,
    sdists: list[Path] | None = None,
    release: bool = False,
    remote_refs_attested: bool = False,
) -> tuple[int, AuditResult]:
    scanner_path = scanner_path.resolve()
    version, digest = check_scanner(scanner_path)
    digest_problem = _scanner_digest_problem(digest)
    if release and digest_problem is not None:
        raise AuditError("gitleaks-digest-mismatch")
    rules = load_denylist(denylist, repo)
    if release and not rules:
        raise AuditError("denylist-required")
    top = _git(repo, "rev-parse", "--show-toplevel").decode("utf-8", "replace").strip()
    if Path(top).resolve() != repo.resolve():
        raise AuditError("repository-root-mismatch")
    if _git(repo, "rev-parse", "--is-shallow-repository").strip() == b"true":
        raise AuditError("shallow-history")
    grafts = Path(os.fsdecode(_git(repo, "rev-parse", "--git-path", "info/grafts").strip()))
    if not grafts.is_absolute():
        grafts = repo / grafts
    if (
        grafts.exists()
        or _git(repo, "for-each-ref", "--format=%(refname)", "refs/replace/").strip()
    ):
        raise AuditError("history-overlays")
    raw_refs = _git(repo, "for-each-ref", "--format=%(refname)")
    if len(raw_refs) > MAX_METADATA_FILE_BYTES:
        raise AuditError("ref-cap")
    refs = tuple(
        sorted(
            _opaque_location("ref", line.decode("utf-8", "replace"))
            for line in raw_refs.splitlines()
            if line
        )
    )
    if len(refs) > MAX_REFS:
        raise AuditError("ref-cap")

    work = Path(tempfile.mkdtemp(prefix="cheeseless-release-audit-"))
    os.chmod(work, 0o700)
    try:
        stage = work / "tracked-tree"
        stage.mkdir(mode=0o700)
        findings = stage_tracked_tree(repo, stage, rules)[0]
        findings.extend(_denylist_text(rules, raw_refs.decode("utf-8", "replace"), "history:refs"))
        _write_private(stage / "release-audit-refs.txt", raw_refs)
        findings.extend(history_metadata(repo, stage, rules))
        config = write_config(work)
        findings.extend(scan_with_gitleaks(scanner_path, config, "dir", stage, work))
        findings.extend(scan_with_gitleaks(scanner_path, config, "git", repo, work))

        if wheels is not None or sdists is not None:
            if not wheels or not sdists:
                raise AuditError("build-output-incomplete")
            archives = _provided_archives(wheels, sdists)
            kinds = {path: ("wheel" if path.suffix == ".whl" else "sdist") for path in archives}
        else:
            built = _build_archives(repo, work / "dist")
            kinds = {path: ("wheel" if path.suffix == ".whl" else "sdist") for path in built}
        expected = _expected_modules(repo)
        archive_info: list[ArchiveInfo] = []
        for archive, kind in kinds.items():
            archive_findings, info = inspect_archive(
                archive, kind, work, scanner_path, config, rules, expected
            )
            findings.extend(archive_findings)
            archive_info.append(info)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if release and not remote_refs_attested:
        findings.append(Finding("hygiene", "remote-refs-unattested", "remote-refs"))

    blocked: list[str] = []
    if len(findings) > MAX_FINDINGS:
        blocked.append("findings-cap")
    aggregated = _aggregate(findings)
    status = "blocked" if aggregated or blocked else "clean"
    result = AuditResult(
        status=status,
        scanner_version=version,
        scanner_sha256=digest,
        refs=refs,
        archives=tuple(archive_info),
        findings=aggregated,
        blocked=tuple(blocked),
        limitations=_limitations((digest_problem,) if digest_problem is not None else ()),
        release_mode=release,
        remote_refs_attested=remote_refs_attested,
    )
    write_report(report_dir, repo, result)
    return (1 if status == "blocked" else 0), result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release-audit",
        description="Run the pinned, fully redacted local release audit.",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--gitleaks", type=Path, default=None, metavar="PATH")
    parser.add_argument("--denylist", type=Path, default=None, metavar="TOML")
    parser.add_argument("--report-dir", type=Path, default=None, metavar="DIR")
    parser.add_argument("--wheel", type=Path, action="append", default=None, metavar="FILE")
    parser.add_argument("--sdist", type=Path, action="append", default=None, metavar="FILE")
    parser.add_argument(
        "--release",
        action="store_true",
        help="strict operator gate before publication (not used by CI)",
    )
    parser.add_argument(
        "--remote-refs-attested",
        action="store_true",
        help=(
            "with --release, assert the operator verified that server-side refs "
            "(e.g. refs/pull/*) no longer expose pre-rewrite commits"
        ),
    )
    return parser


def _default_report_dir() -> Path:
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / "cheeseless" / "release-audit"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo.expanduser().resolve()
    scanner = args.gitleaks or Path(os.environ.get("GITLEAKS_BIN", "gitleaks"))
    report_dir = args.report_dir or _default_report_dir()
    try:
        code, result = audit(
            repo=repo,
            scanner_path=scanner.expanduser(),
            report_dir=report_dir,
            denylist=args.denylist,
            wheels=args.wheel,
            sdists=args.sdist,
            release=args.release,
            remote_refs_attested=args.remote_refs_attested,
        )
    except AuditError as exc:
        print(f"release audit: FAILED CLOSED ({exc.code})")
        try:
            failure = AuditResult(
                status="blocked",
                scanner_version="unknown",
                scanner_sha256="",
                refs=(),
                archives=(),
                findings=(),
                blocked=(exc.code,),
                limitations=_limitations(),
                release_mode=args.release,
                remote_refs_attested=args.remote_refs_attested,
            )
            write_report(report_dir, repo, failure)
            print("report: private metadata-only report written outside the repository")
        except AuditError:
            print("report: not written")
        return 2
    except Exception:  # noqa: BLE001 - fail closed without a traceback or raw text
        print("release audit: FAILED CLOSED (internal-error)")
        return 2
    _print_summary(result, True)
    return code


if __name__ == "__main__":
    sys.exit(main())
