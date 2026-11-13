import json
import os
from pathlib import Path

import pytest

from cheeseless.domain.egress_policy import PolicyError
from cheeseless.session_options import (
    SessionOptionError,
    check_extra_env,
    check_extra_read_only,
    check_home_seed,
    ensure_outside_all,
    load_egress_policy,
    parse_key_value,
    parse_seed_spec,
    within,
)

RESERVED = ("CHEESELESS_SOCKET", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE", "HOME", "PATH")


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "repo": tmp_path / "repo",
        "store": tmp_path / "state" / "cheeseless",
        "ro": tmp_path / "ro",
        "sub": tmp_path / "repo" / "src",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    (paths["ro"] / "tool.sh").write_text("#!/bin/sh\n")
    return {**paths, "root": tmp_path}


def protected(layout: dict[str, Path]) -> tuple[Path, ...]:
    return (layout["repo"], layout["store"])


def pinned_paths(mounts) -> tuple[Path, ...]:
    try:
        return tuple(mount.path for mount in mounts)
    finally:
        for mount in mounts:
            os.close(mount.fd)


def test_within_resolves_symlinks(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert within(tmp_path / "link" / "x", tmp_path / "real")
    assert not within(tmp_path / "real", tmp_path / "link" / "x")


def test_ensure_outside_all_rejects_inside(layout: dict[str, Path]) -> None:
    with pytest.raises(SessionOptionError, match="evidence"):
        ensure_outside_all(layout["sub"] / "out", protected(layout), "evidence directory")
    ensure_outside_all(layout["ro"] / "out", protected(layout), "evidence directory")


def test_read_only_accepts_unrelated_dir(layout: dict[str, Path]) -> None:
    result = check_extra_read_only([layout["ro"]], protected(layout))
    assert pinned_paths(result) == (layout["ro"].resolve(),)


@pytest.mark.parametrize("key", ["repo", "sub", "store", "root"])
def test_read_only_rejects_forbidden_and_ancestors(layout: dict[str, Path], key: str) -> None:
    with pytest.raises(SessionOptionError):
        check_extra_read_only([layout[key]], protected(layout))


def test_read_only_rejects_symlink_into_repo(layout: dict[str, Path]) -> None:
    link = layout["ro"] / "sneaky"
    link.symlink_to(layout["repo"])
    with pytest.raises(SessionOptionError):
        check_extra_read_only([link], protected(layout))


def test_read_only_rejects_missing_path(layout: dict[str, Path]) -> None:
    with pytest.raises(SessionOptionError, match="exist"):
        check_extra_read_only([layout["ro"] / "nope"], protected(layout))


def test_read_only_rejects_ancestor_of_temp_base(layout: dict[str, Path]) -> None:
    base = layout["root"] / "tmpbase"
    base.mkdir()
    with pytest.raises(SessionOptionError):
        check_extra_read_only([layout["root"]], (), temp_bases=(base,))
    assert pinned_paths(check_extra_read_only([base], (), temp_bases=(base.parent,))) == (
        base.resolve(),
    )


def test_home_seed_accepts_regular_file(layout: dict[str, Path]) -> None:
    src = layout["ro"] / "cred.json"
    src.write_text("{}")
    assert check_home_seed({".cfg/cred.json": src}, protected(layout)) == (
        (".cfg/cred.json", b"{}"),
    )


@pytest.mark.parametrize("rel", ["/abs", "../up", "a/../../b", "", ".", "a//b/..", "a\0b"])
def test_home_seed_rejects_bad_rel(layout: dict[str, Path], rel: str) -> None:
    src = layout["ro"] / "f"
    src.write_text("x")
    with pytest.raises(SessionOptionError):
        check_home_seed({rel: src}, protected(layout))


def test_home_seed_rejects_source_in_repo(layout: dict[str, Path]) -> None:
    secret = layout["repo"] / "hidden.py"
    secret.write_text("canary")
    with pytest.raises(SessionOptionError):
        check_home_seed({"x": secret}, protected(layout))


def test_home_seed_rejects_dir_and_missing_and_symlink_to_repo(layout: dict[str, Path]) -> None:
    with pytest.raises(SessionOptionError):
        check_home_seed({"x": layout["ro"]}, protected(layout))
    with pytest.raises(SessionOptionError):
        check_home_seed({"x": layout["ro"] / "missing"}, protected(layout))
    target = layout["repo"] / "t"
    target.write_text("s")
    link = layout["ro"] / "l"
    link.symlink_to(target)
    with pytest.raises(SessionOptionError):
        check_home_seed({"x": link}, protected(layout))


def test_home_seed_rejects_conflicting_entries(layout: dict[str, Path]) -> None:
    src = layout["ro"] / "f"
    src.write_text("x")
    with pytest.raises(SessionOptionError, match="conflict"):
        check_home_seed({"a": src, "a/b": src}, protected(layout))


def test_extra_env_accepts_additive_keys() -> None:
    result = check_extra_env({"ANTHROPIC_API_KEY": "k", "lower_ok": "v"}, RESERVED)
    assert dict(result) == {"ANTHROPIC_API_KEY": "k", "lower_ok": "v"}


@pytest.mark.parametrize(
    "key",
    [
        "HTTPS_PROXY",
        "https_proxy",
        "no_proxy",
        "CHEESELESS_X",
        "cheeseless_x",
        "HOME",
        "PATH",
        "TMPDIR",
        "LANG",
        "SSL_CERT_FILE",
        "NODE_EXTRA_CA_CERTS",
        "CODEX_CA_CERTIFICATE",
        "LD_PRELOAD",
        "ALL_PROXY",
        "all_proxy",
    ],
)
def test_extra_env_rejects_reserved(key: str) -> None:
    with pytest.raises(SessionOptionError) as err:
        check_extra_env({key: "super-secret-value"}, RESERVED)
    assert "super-secret-value" not in str(err.value)


@pytest.mark.parametrize(
    "key,value", [("A\0B", "v"), ("A=B", "v"), ("", "v"), ("1A", "v"), ("OK", "a\0b")]
)
def test_extra_env_rejects_malformed_without_leaking(key: str, value: str) -> None:
    with pytest.raises(SessionOptionError) as err:
        check_extra_env({key: value}, RESERVED)
    assert value not in str(err.value) or value == "v"


def test_parse_key_value_keeps_equals_in_value() -> None:
    assert parse_key_value("KEY=a=b") == ("KEY", "a=b")
    assert parse_key_value("KEY=") == ("KEY", "")


@pytest.mark.parametrize("raw", ["NOEQUALS", "=value", ""])
def test_parse_key_value_rejects(raw: str) -> None:
    with pytest.raises(SessionOptionError) as err:
        parse_key_value(raw)
    assert "NOEQUALS" not in str(err.value) or raw == "NOEQUALS"


def test_parse_seed_spec() -> None:
    assert parse_seed_spec(".a/b=/some/src") == (".a/b", Path("/some/src"))
    with pytest.raises(SessionOptionError):
        parse_seed_spec("norel")
    with pytest.raises(SessionOptionError):
        parse_seed_spec("=/x")


def test_load_egress_policy_json_and_toml(tmp_path: Path) -> None:
    data = {
        "allowed_hosts": ["Example.com", "api.example.com"],
        "model_hosts": ["api.example.com"],
    }
    as_json = tmp_path / "p.json"
    as_json.write_text(json.dumps(data))
    as_toml = tmp_path / "p.toml"
    as_toml.write_text(
        'allowed_hosts = ["Example.com", "api.example.com"]\nmodel_hosts = ["api.example.com"]\n'
    )
    for path in (as_json, as_toml):
        policy = load_egress_policy(path)
        assert "example.com" in policy.allowed_hosts
        assert "api.example.com" in policy.model_hosts


def test_load_egress_policy_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "p.json"
    bad.write_text('{"allowed_hosts": "nope"}')
    with pytest.raises(PolicyError):
        load_egress_policy(bad)
    garbage = tmp_path / "p.toml"
    garbage.write_text("= not toml")
    with pytest.raises(SessionOptionError):
        load_egress_policy(garbage)
    with pytest.raises(SessionOptionError):
        load_egress_policy(tmp_path / "missing.json")
    other = tmp_path / "p.yaml"
    other.write_text("a: b")
    with pytest.raises(SessionOptionError, match="extension"):
        load_egress_policy(other)


def test_home_seed_rejects_multiply_linked_source(layout: dict[str, Path]) -> None:
    source = layout["ro"] / "seed"
    source.write_text("x")
    os.link(source, layout["ro"] / "seed-link")
    with pytest.raises(SessionOptionError, match="multiple links"):
        check_home_seed({"x": source}, protected(layout))


def test_home_seed_rejects_oversized_source(layout: dict[str, Path]) -> None:
    source = layout["ro"] / "big"
    source.write_bytes(b"x" * ((1 << 20) + 1))
    with pytest.raises(SessionOptionError, match="too large"):
        check_home_seed({"x": source}, protected(layout))


def test_read_only_rejects_reserved_and_sandbox_tmp(layout: dict[str, Path]) -> None:
    with pytest.raises(SessionOptionError, match="reserved"):
        check_extra_read_only([Path("/proc")], (), reserved_mounts=("/proc",))
    with pytest.raises(SessionOptionError, match="temporary"):
        check_extra_read_only([layout["root"]], (), sandbox_tmp=str(layout["root"] / "t"))


def test_prepare_extras_closes_mounts_when_a_later_check_fails(
    layout: dict[str, Path], monkeypatch
) -> None:
    from cheeseless.session_options import PinnedMount, prepare_extras

    fd = os.open(layout["ro"], os.O_PATH | os.O_CLOEXEC)
    pinned = (PinnedMount(fd=fd, path=layout["ro"]),)
    monkeypatch.setattr(
        "cheeseless.session_options.check_extra_read_only", lambda *args, **kwargs: pinned
    )
    with pytest.raises(SessionOptionError):
        prepare_extras(
            protected=protected(layout),
            temp_bases=(),
            reserved_env=RESERVED,
            extra_read_only=[layout["ro"]],
            home_seed={"../escape": layout["ro"] / "tool.sh"},
        )
    with pytest.raises(OSError):
        os.fstat(fd)
