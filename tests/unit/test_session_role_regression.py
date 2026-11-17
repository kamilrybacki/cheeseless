"""The implementer agent-visible surface is byte-identical to the pre-role behaviour."""

from pathlib import Path

from cheeseless.adapters.inbound.mcp.server import build_mcp_server
from cheeseless.adapters.outbound.sandbox import profiles
from cheeseless.bootstrap import _mcp_shim_source
from cheeseless.domain.session_role import SessionRole, acceptance_mount


class _C:
    pass


def test_the_implementer_shim_source_is_unchanged():
    assert _mcp_shim_source("/py", Path("/site"), Path("/s.sock"), "t1") == (
        "#!/py\nimport sys\nsys.path.insert(0, '/site')\n"
        "from cheeseless.bootstrap import serve_mcp\n"
        "raise SystemExit(serve_mcp('/s.sock', task_id='t1', disputes=False))\n"
    )
    assert _mcp_shim_source(
        "/py", Path("/site"), Path("/s.sock"), "t1", role=SessionRole.IMPLEMENTER
    ) == _mcp_shim_source("/py", Path("/site"), Path("/s.sock"), "t1")


def test_other_roles_bake_a_role_literal():
    src = _mcp_shim_source("/py", Path("/site"), Path("/s.sock"), "t1", role=SessionRole.AUTHOR)
    assert "role='author'" in src


def test_the_implementer_tool_set_is_unchanged():
    default = build_mcp_server(_C())
    explicit = build_mcp_server(_C(), role=SessionRole.IMPLEMENTER)
    names = lambda s: [t.name for t in s._tool_manager.list_tools()]  # noqa: E731
    assert names(default) == names(explicit) == ["gate_run", "gate_status"]


def test_the_implementer_acceptance_mount_is_unchanged(tmp_path):
    proj = tmp_path / "p"
    (proj / "acceptance").mkdir(parents=True)
    fd = profiles.open_acceptance(proj)
    common = dict(
        bwrap="bwrap", projection=proj, sockets=[], home=tmp_path / "h",
        read_only=[], env=[], command=["true"], acceptance_fd=fd,
    )  # fmt: skip
    try:
        assert profiles.agent(**common) == profiles.agent(
            **common, acceptance_mode=acceptance_mount(SessionRole.IMPLEMENTER)
        )
    finally:
        import os

        os.close(fd)
