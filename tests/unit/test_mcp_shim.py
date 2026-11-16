"""The staged MCP shim bakes safely quoted socket/task literals, never env lookups."""

from cheeseless.bootstrap import _mcp_shim_source


def test_shim_source_has_no_environment_lookup(tmp_path):
    site = tmp_path / "site"
    socket_path = tmp_path / "v.sock"
    source = _mcp_shim_source("/usr/bin/python3", site, socket_path, "a" * 64)
    assert "os.environ" not in source
    assert "CHEESELESS_SOCKET" not in source
    assert "CHEESELESS_TASK" not in source
    assert repr(str(socket_path)) in source
    assert repr("a" * 64) in source
    compile(source, "cheeseless-mcp", "exec")


def test_hostile_quoting_cannot_break_out_of_the_literals(tmp_path):
    socket_path = tmp_path / "weird'name" / '../"sock"\n'
    task_id = "t'\"\\\n#not-a-comment"
    source = _mcp_shim_source("/usr/bin/python3", tmp_path / "it's", socket_path, task_id)
    assert repr(str(socket_path)) in source
    assert repr(task_id) in source
    compile(source, "cheeseless-mcp", "exec")


def test_shim_bakes_the_dispute_switch_as_a_literal(tmp_path):
    socket_path = tmp_path / "v.sock"
    off = _mcp_shim_source("/usr/bin/python3", tmp_path, socket_path, "a" * 64)
    on = _mcp_shim_source("/usr/bin/python3", tmp_path, socket_path, "a" * 64, disputes=True)
    assert "disputes=False" in off
    assert "disputes=True" in on
    compile(on, "cheeseless-mcp", "exec")


def test_host_reads_the_dispute_switch_from_the_trusted_store_fail_closed(tmp_path):
    from cheeseless.adapters.outbound.store.file_store import FileStore
    from cheeseless.app.dispute import configure_disputes
    from cheeseless.bootstrap import _disputes_enabled

    store = FileStore(tmp_path / "store")
    assert _disputes_enabled(store) is False
    configure_disputes(store, enabled=True, cap=3)
    assert _disputes_enabled(store) is True
    (tmp_path / "store" / "dispute-policy.json").write_text("{not json", encoding="utf-8")
    assert _disputes_enabled(store) is False
