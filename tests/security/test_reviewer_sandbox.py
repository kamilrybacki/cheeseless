"""The reviewer sandbox: reads the tests, writes nothing, reaches nothing (ADR 0015).

`agent()` binds the projection writable because an implementer must edit code. The reviewer
must not, so it gets its own argv builder and these tests pin the difference. A reviewer that
inherited the implementer's writable bind could edit the very tests it is judging.
"""

from __future__ import annotations

import os

import pytest

from cheeseless.adapters.outbound.sandbox import profiles
from cheeseless.domain.session_role import SessionRole


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


def _session_argv(role):  # type: ignore[no-untyped-def]
    """Run the real chooser `host_session` uses, with a valid-looking environment."""
    from pathlib import Path

    from cheeseless import bootstrap

    return bootstrap._session_argv(
        role,
        bwrap="bwrap",
        projection=Path("projection"),
        home=Path("home"),
        sockets=[Path("sock/verifier.sock"), Path("egress/egress.sock")],
        read_only=[Path("runtime"), Path("python")],
        acceptance_fd=11,
        acceptance_mode="ro",
        pinned_read_only=(),
        env=(("PATH", "/usr/bin"),),
        command=["true"],
        seccomp_fd=12,
        info_fd=13,
    )


def test_an_implementer_session_gets_the_agent_profile() -> None:
    """The control for the pair below.

    Without it, an implementation that gave every role the reviewer profile would look like
    it passed the reviewer test. Found in review: the reviewer choice had no behavioural test
    at all, so reverting it to `agent` - writable projection, network, sockets - stayed green.
    """
    argv = _session_argv(SessionRole.IMPLEMENTER)
    assert any(a.endswith("verifier.sock") for a in argv), "an implementer keeps its sockets"
    assert any(a.endswith("egress.sock") for a in argv), "and its egress socket"
    assert any(a == "projection" for a in argv), "and a writable projection"


def test_a_reviewer_session_gets_the_reviewer_profile() -> None:
    """The reviewer must reach the reviewer builder, not the writable agent one."""
    argv = _session_argv(SessionRole.REVIEWER)
    assert not any("verifier.sock" in a for a in argv), "a reviewer gets no verifier socket"
    assert not any("egress.sock" in a for a in argv), "a reviewer gets no egress socket"
    assert "true" in argv, "the command reaches the sandbox unwrapped"


def test_the_rpc_dispatcher_refuses_a_reviewer_role() -> None:
    """Defence in depth: the reviewer has no socket, and would be refused if it had one.

    The sandbox not mounting a socket is the real protection; this proves the guarantee does
    not depend on the mount staying correct, so a future change that hands a reviewer an
    endpoint does not silently give it RPC.
    """
    from types import SimpleNamespace

    from cheeseless.adapters.inbound.uds.server import UdsVerifierServer

    server = UdsVerifierServer.__new__(UdsVerifierServer)
    server.session = SimpleNamespace(role="reviewer", task_id="t", generation=0)
    for kind in ("submit", "result", "dispute", "reference_done"):
        assert server._role_ok("reviewer", kind) is False, f"a reviewer must not reach {kind}"
