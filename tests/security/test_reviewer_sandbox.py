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
    """A property, not a list of names: nothing outside the allowlist survives, ever."""
    from cheeseless import bootstrap

    env = [
        ("CHEESELESS_RUNTIME", "/rt"),
        ("CHEESELESS_MCP", "/mcp"),
        ("CHEESELESS_SOCKET", "/s.sock"),
        ("CHEESELESS_TASK", "t1"),
        ("CHEESELESS_EGRESS_READY", "/ready"),
        ("CHEESELESS_EGRESS_PORT", "18080"),
        ("EGRESS_SOCKET_ENV", "/e.sock"),
        ("HTTPS_PROXY", "http://p:1"),
        ("SSL_CERT_FILE", "/ca"),
        ("GIT_CONFIG_GLOBAL", "/candidate/.gitconfig"),
        ("ANY_VARIABLE_ADDED_LATER", "leak"),
    ]
    kept = bootstrap._reviewer_env(env)
    allowed = {"CHEESELESS_RUNTIME", "CHEESELESS_MCP"}
    assert {k for k, _ in kept} == allowed
    assert dict(kept)["CHEESELESS_RUNTIME"] == "/rt"


def test_the_reviewer_argv_is_built_without_sockets(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Even if a caller passes sockets, none of them becomes a mount."""
    sock = tmp_path / "verifier.sock"
    sock.write_text("")
    args, fd = _common(tmp_path, sockets=[sock])
    try:
        argv = profiles.reviewer(**args)
    finally:
        os.close(fd)
    for flag in ("--bind", "--ro-bind", "--bind-try", "--ro-bind-try"):
        assert not [p for p, _ in _pairs(argv, flag) if p.endswith(".sock")], flag
    assert str(sock) not in argv


# -- the protections must fail when removed -----------------------------------


def test_the_reviewer_really_reads_the_tests_inside_the_sandbox(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A positive control that runs the command and reads a canary.

    Found in review: asserting that *some* mount exists proves nothing, because HOME would
    satisfy it. A write-denial test is only meaningful if the sandbox starts and the tests are
    actually readable, so this runs the real command through the real argv.
    """
    import subprocess

    canary = "CANARY-READ-9c1e"
    proj = tmp_path / "proj"
    (proj / "acceptance" / "hidden").mkdir(parents=True)
    (proj / "acceptance" / "hidden" / "test_hidden.py").write_text(canary)
    home = tmp_path / "home"
    home.mkdir()
    fd = profiles.open_acceptance(proj)
    try:
        argv = profiles.reviewer(
            bwrap="/usr/bin/bwrap",
            projection=proj,
            home=home,
            env=[],
            command=["/bin/sh", "-c", "cat acceptance/hidden/test_hidden.py"],
            acceptance_fd=fd,
        )
        out = subprocess.run(  # noqa: S603
            argv, capture_output=True, pass_fds=(fd,), timeout=60
        )
    finally:
        os.close(fd)
    assert out.returncode == 0, out.stderr
    assert canary in out.stdout.decode()


def test_the_reviewer_mount_refuses_to_lie_about_a_missing_projection(tmp_path) -> None:  # type: ignore[no-untyped-def]
    args, fd = _common(tmp_path)
    os.close(fd)
    args["acceptance_fd"] = None
    with pytest.raises(ValueError):
        profiles.reviewer(**args)


def test_the_home_overlap_is_refused_rather_than_commented_about(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Found in review: a writable HOME above the projection would shadow it."""
    from cheeseless import bootstrap
    from cheeseless.session_options import SessionOptionError

    outer = tmp_path / "outer"
    inner = outer / "proj"
    inner.mkdir(parents=True)
    with pytest.raises(SessionOptionError):
        bootstrap._refuse_overlap(outer, inner)
    with pytest.raises(SessionOptionError):
        bootstrap._refuse_overlap(inner, outer)
    sibling = tmp_path / "other"
    sibling.mkdir()
    bootstrap._refuse_overlap(inner, sibling)  # disjoint: no error


def test_the_environment_whitelist_admits_only_what_a_model_session_needs() -> None:
    from cheeseless import bootstrap

    env = [
        ("CHEESELESS_RUNTIME", "/rt"),
        ("CHEESELESS_MCP", "/mcp"),
        ("CHEESELESS_SOCKET", "/s.sock"),
        ("CHEESELESS_EGRESS_READY", "/ready"),
        ("CHEESELESS_EGRESS_PORT", "18080"),
        ("HTTPS_PROXY", "http://p:1"),
        ("GIT_CONFIG_GLOBAL", "/candidate/.gitconfig"),
        ("SOMETHING_NEW_ADDED_LATER", "leak"),
    ]
    assert bootstrap._reviewer_env(env) == [
        ("CHEESELESS_RUNTIME", "/rt"),
        ("CHEESELESS_MCP", "/mcp"),
    ]


def test_a_reviewer_session_waits_for_no_egress_bridge() -> None:
    """Found in review: the reviewer was launched behind the bridge wrapper and hung."""
    import inspect

    from cheeseless import bootstrap

    source = inspect.getsource(bootstrap.host_session)
    # The readiness handshake is guarded, not unconditional: an isolated reviewer can never
    # produce the marker, so waiting for it would block the session until the timeout.
    assert "if bound is not SessionRole.REVIEWER:" in source
    assert "_wait_for_bridge(egress_ready, proc)" in source
    # And it is not wrapped by the bridge command either.
    assert "if bound is SessionRole.REVIEWER:" in source
