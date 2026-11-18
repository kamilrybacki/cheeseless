"""The reviewer sandbox: reads the tests, writes nothing, reaches nothing (ADR 0015).

`agent()` binds the projection writable because an implementer must edit code. The reviewer
must not, so it gets its own argv builder and these tests pin the difference. A reviewer that
inherited the implementer's writable bind could edit the very tests it is judging.
"""

from __future__ import annotations

import os

import pytest

from cheeseless.adapters.outbound.sandbox import profiles


def _common(tmp_path, **kw):
    proj = tmp_path / "proj"
    (proj / "acceptance").mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    fd = profiles.open_acceptance(proj)
    base = dict(
        bwrap="bwrap",
        projection=proj,
        home=home,
        env=[],
        command=["true"],
        acceptance_fd=fd,
        seccomp_fd=3,
    )
    base.update(kw)
    return base, fd


def _pairs(argv: list[str], flag: str) -> list[tuple[str, str]]:
    return [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == flag]


def test_the_reviewer_sees_the_projection_read_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    args, fd = _common(tmp_path)
    try:
        argv = profiles.reviewer(**args)
    finally:
        os.close(fd)
    proj = str(args["projection"])
    # Whichever way it is expressed, the projection itself is never a writable bind.
    writable = [p for p, _ in _pairs(argv, "--bind") if p == proj]
    assert writable == []
    read_only = {p for p, _ in _pairs(argv, "--ro-bind")}
    assert proj in read_only


def test_the_reviewer_cannot_write_acceptance_even_inside_a_read_only_projection(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    args, fd = _common(tmp_path)
    try:
        argv = profiles.reviewer(**args)
    finally:
        os.close(fd)
    assert ["--ro-bind-fd", str(fd), str(args["projection"] / "acceptance")] == argv[
        argv.index("--ro-bind-fd") : argv.index("--ro-bind-fd") + 3
    ]


def test_the_reviewer_gets_no_socket(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A reviewer talks to nobody: no broker, no verifier socket, no host endpoint."""
    args, fd = _common(tmp_path, sockets=[tmp_path / "s.sock"])
    try:
        argv = profiles.reviewer(**args)
    finally:
        os.close(fd)
    assert "--bind" not in argv or not any(p.endswith(".sock") for p, _ in _pairs(argv, "--bind"))


def test_the_reviewer_home_is_bound_writable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The agent's own HOME must be writable, or the tool it runs cannot start."""
    args, fd = _common(tmp_path)
    try:
        argv = profiles.reviewer(**args)
    finally:
        os.close(fd)
    assert str(args["home"]) in {p for p, _ in _pairs(argv, "--bind")}


def test_the_reviewer_fails_closed_without_a_pinned_acceptance_fd(tmp_path) -> None:  # type: ignore[no-untyped-def]
    args, fd = _common(tmp_path)
    os.close(fd)
    args["acceptance_fd"] = None
    with pytest.raises(ValueError, match="acceptance"):
        profiles.reviewer(**args)


def test_the_reviewer_starts_in_the_projection(tmp_path) -> None:  # type: ignore[no-untyped-def]
    args, fd = _common(tmp_path)
    try:
        argv = profiles.reviewer(**args)
    finally:
        os.close(fd)
    assert argv[argv.index("--chdir") + 1] == str(args["projection"])


def test_the_reviewer_runs_the_command_it_was_given(tmp_path) -> None:  # type: ignore[no-untyped-def]
    args, fd = _common(tmp_path, command=["reviewer-binary", "--flag"])
    try:
        argv = profiles.reviewer(**args)
    finally:
        os.close(fd)
    assert argv[-2:] == ["reviewer-binary", "--flag"]


def test_the_reviewer_rejects_an_empty_command(tmp_path) -> None:  # type: ignore[no-untyped-def]
    args, fd = _common(tmp_path, command=[])
    try:
        with pytest.raises(Exception, match="command"):
            profiles.reviewer(**args)
    finally:
        os.close(fd)


def test_the_reviewer_argv_differs_from_the_implementer_in_the_projection_bind(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    """The one difference that matters, asserted as a difference rather than a shape."""
    args, fd = _common(tmp_path)
    try:
        impl = profiles.agent(**args, sockets=[], read_only=[], acceptance_mode="ro")
        rev = profiles.reviewer(**args)
    finally:
        os.close(fd)
    proj = str(args["projection"])
    assert (proj, proj) in _pairs(impl, "--bind")  # the implementer writes there
    assert (proj, proj) not in _pairs(rev, "--bind")


# -- the environment a reviewer may not inherit -------------------------------


def test_the_reviewer_environment_carries_no_channel_off_the_tests() -> None:
    from cheeseless import bootstrap

    env = [
        ("CHEESELESS_SOCKET", "/s.sock"),
        ("CHEESELESS_TASK", "t1"),
        ("CHEESELESS_RUNTIME", "/rt"),
        ("EGRESS_SOCKET_ENV", "/e.sock"),
        ("HTTPS_PROXY", "http://p:1"),
        ("HTTP_PROXY", "http://p:1"),
        ("ALL_PROXY", "http://p:1"),
        ("https_proxy", "http://p:1"),
        ("http_proxy", "http://p:1"),
        ("all_proxy", "http://p:1"),
        ("SSL_CERT_FILE", "/ca"),
        ("NODE_EXTRA_CA_CERTS", "/ca"),
        ("CODEX_CA_CERTIFICATE", "/ca"),
    ]
    kept = dict(bootstrap._reviewer_env(env))
    for stripped in (
        "CHEESELESS_SOCKET",
        "CHEESELESS_TASK",
        "EGRESS_SOCKET_ENV",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
        "SSL_CERT_FILE",
        "NODE_EXTRA_CA_CERTS",
        "CODEX_CA_CERTIFICATE",
    ):
        assert stripped not in kept, stripped
    # It still needs to find its own interpreter and runtime.
    assert kept["CHEESELESS_RUNTIME"] == "/rt"


def test_the_reviewer_argv_is_built_without_sockets() -> None:
    """Even if a caller passes sockets, `reviewer()` never turns them into a bind."""
    import inspect

    from cheeseless.adapters.outbound.sandbox import profiles as p

    source = inspect.getsource(p.reviewer)
    assert "sockets" in source  # accepted, so callers can share plumbing
    assert '--bind", str(s' not in source
