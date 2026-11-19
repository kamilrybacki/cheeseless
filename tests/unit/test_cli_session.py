from pathlib import Path

import pytest

from cheeseless.adapters.inbound.cli.main import build_parser, split_agent_argv


def parse(*argv: str):
    head, agent = split_agent_argv(list(argv))
    return build_parser().parse_args(head), agent


def test_split_agent_argv_at_first_double_dash() -> None:
    head, agent = split_agent_argv(["session", "--task", "t", "--", "claude", "--", "-x"])
    assert head == ["session", "--task", "t"]
    assert agent == ["claude", "--", "-x"]


def test_split_agent_argv_without_separator() -> None:
    assert split_agent_argv(["session", "--task", "t"]) == (["session", "--task", "t"], None)
    assert split_agent_argv(["status"]) == (["status"], None)


def test_session_defaults() -> None:
    args, agent = parse("session", "--", "agent", "--flag")
    assert args.command == "session"
    assert agent == ["agent", "--flag"]
    assert args.profile == "local"
    assert args.timeout == 300.0
    assert args.egress_policy is None
    assert args.ro == [] and args.home_seed == [] and args.env == []
    assert args.evidence_dir is None
    assert not hasattr(args, "stdio")


def test_session_all_options() -> None:
    args, _ = parse(
        "session",
        "--task",
        "t1",
        "--profile",
        "ci",
        "--timeout",
        "12.5",
        "--egress-policy",
        "p.toml",
        "--ro",
        "/a",
        "--ro",
        "/b",
        "--home-seed",
        ".x=/s",
        "--env",
        "K=V=W",
        "--env",
        "L=1",
        "--evidence-dir",
        "/ev",
        "--state-dir",
        "/st",
        "--",
        "agent",
    )
    assert args.task == "t1" and args.profile == "ci" and args.timeout == 12.5
    assert args.egress_policy == Path("p.toml")
    assert args.ro == [Path("/a"), Path("/b")]
    assert args.home_seed == [".x=/s"]
    assert args.env == ["K=V=W", "L=1"]
    assert args.evidence_dir == Path("/ev")
    assert args.state_dir == Path("/st")


def test_session_rejects_unknown_option_before_separator() -> None:
    with pytest.raises(SystemExit):
        parse("session", "--bogus", "--", "agent")


# -- bootstrap wiring ---------------------------------------------------------------------


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake(**kwargs: object) -> int:
        calls.append(kwargs)
        return 7

    monkeypatch.setattr("cheeseless.bootstrap.launch_host_session", fake)
    return calls


def test_main_session_maps_options(
    launched: list[dict[str, object]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from cheeseless.bootstrap import main

    policy = tmp_path / "p.json"
    policy.write_text('{"allowed_hosts": ["example.com"]}')
    ro = tmp_path / "ro"
    ro.mkdir()
    seed = tmp_path / "seed"
    seed.write_text("x")
    code = main(
        [
            "session",
            "--task",
            "t1",
            "--profile",
            "ci",
            "--timeout",
            "9",
            "--egress-policy",
            str(policy),
            "--ro",
            str(ro),
            "--home-seed",
            f".cfg/s={seed}",
            "--env",
            "TOKEN=top-secret",
            "--evidence-dir",
            str(tmp_path / "ev"),
            "--",
            "agent",
            "--run",
        ]
    )
    assert code == 7
    (call,) = launched
    assert call["task_id"] == "t1" and call["profile"] == "ci" and call["timeout"] == 9.0
    assert call["agent_command"] == ["agent", "--run"]
    assert call["agent_stdio"] is False
    assert call["extra_read_only"] == (ro,)
    assert call["home_seed"] == {".cfg/s": seed}
    assert call["extra_env"] == {"TOKEN": "top-secret"}
    assert call["evidence_dir"] == tmp_path / "ev"
    assert "example.com" in call["egress_policy"].allowed_hosts  # type: ignore[attr-defined]
    assert "top-secret" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["session", "--task", "t"],
        ["session", "--task", "t", "--"],
        ["session", "--env", "NOEQUALS", "--", "agent"],
        ["session", "--home-seed", "norel", "--", "agent"],
        ["session", "--env", "HTTPS_PROXY=x", "--", "agent"],
        ["session", "--egress-policy", "/nonexistent/p.json", "--", "agent"],
    ],
)
def test_main_session_usage_errors(
    launched: list[dict[str, object]], argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    from cheeseless.bootstrap import main

    assert main(argv) == 2
    assert launched == []
    assert capsys.readouterr().err.strip()


def test_main_session_reports_rejection_without_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from cheeseless.bootstrap import main
    from cheeseless.session_options import SessionOptionError

    def boom(**_: object) -> int:
        raise SessionOptionError("mount rejected")

    monkeypatch.setattr("cheeseless.bootstrap.launch_host_session", boom)
    assert main(["session", "--env", "K=top-secret", "--", "agent"]) == 2
    captured = capsys.readouterr()
    assert "mount rejected" in captured.err
    assert "top-secret" not in captured.err + captured.out


def test_session_rejects_stdio_flag() -> None:
    """The terminal is always inherited; a pipe mode nobody reads would swallow output."""
    with pytest.raises(SystemExit):
        parse("session", "--stdio", "--", "agent")


@pytest.mark.parametrize("command", ["session", "serve-uds"])
def test_global_state_dir_survives_subcommand(command: str) -> None:
    head = ["--state-dir", "/global", command]
    args, _ = parse(*head, "--", "agent") if command == "session" else parse(*head)
    assert args.state_dir == Path("/global")


@pytest.mark.parametrize("command", ["session", "serve-uds"])
def test_subcommand_state_dir_overrides_global(command: str) -> None:
    head = ["--state-dir", "/global", command, "--state-dir", "/local"]
    args, _ = parse(*head, "--", "agent") if command == "session" else parse(*head)
    assert args.state_dir == Path("/local")


def test_main_session_global_state_dir_reaches_launch(
    launched: list[dict[str, object]],
) -> None:
    from cheeseless.bootstrap import main

    assert main(["--state-dir", "/global", "session", "--", "agent"]) == 7
    assert launched[0]["state_dir"] == Path("/global")


@pytest.mark.parametrize(
    "error",
    [ValueError("no active task"), RuntimeError("stray hidden material in the projection")],
)
def test_main_session_startup_error_exits_2_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    from cheeseless.bootstrap import main

    def boom(**_: object) -> int:
        raise error

    monkeypatch.setattr("cheeseless.bootstrap.launch_host_session", boom)
    assert main(["session", "--", "agent"]) == 2
    err = capsys.readouterr().err
    assert str(error) in err
    assert "Traceback" not in err


def test_main_session_projection_dir_reaches_launch(
    launched: list[dict[str, object]], tmp_path: Path
) -> None:
    """A caller-owned projection survives the session so its tree can be promoted."""
    from cheeseless.bootstrap import main

    assert main(["session", "--projection", str(tmp_path / "p"), "--", "agent"]) == 7
    assert launched[0]["projection_dir"] == tmp_path / "p"


def test_main_session_default_projection_is_owned(launched: list[dict[str, object]]) -> None:
    from cheeseless.bootstrap import main

    assert main(["session", "--", "agent"]) == 7
    assert launched[0]["projection_dir"] is None


def test_main_session_resolves_a_relative_projection(
    launched: list[dict[str, object]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S48: a relative path is made absolute against the cwd, without following symlinks."""
    from cheeseless.bootstrap import main

    monkeypatch.chdir(tmp_path)
    assert main(["session", "--projection", "rel/p", "--", "agent"]) == 7
    assert launched[0]["projection_dir"] == tmp_path / "rel" / "p"
    assert Path(str(launched[0]["projection_dir"])).is_absolute()


def test_main_session_keeps_a_symlinked_projection_visible_as_a_symlink(
    launched: list[dict[str, object]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cheeseless.bootstrap import main

    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    monkeypatch.chdir(tmp_path)
    assert main(["session", "--projection", "link", "--", "agent"]) == 7
    # not resolved: the host's is_symlink refusal must still see the link
    assert launched[0]["projection_dir"] == tmp_path / "link"
    assert Path(str(launched[0]["projection_dir"])).is_symlink()
