import pytest

from cheeseless.bootstrap import main


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("cheeseless ")


def test_no_args_prints_help_and_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) != 0
    assert "usage" in capsys.readouterr().out.lower()


def test_console_entry_uses_process_arguments(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["cheeseless", "--version"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("cheeseless ")
