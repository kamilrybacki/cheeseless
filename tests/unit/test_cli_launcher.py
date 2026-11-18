"""`strength-review --run --launcher NAME` takes command and model from the catalog."""

from types import SimpleNamespace

from cheeseless.adapters.inbound.cli import main as cli
from cheeseless.domain.launcher import Launcher, LauncherError


class _Catalog:
    def get(self, name: str) -> Launcher:
        if name != "gpt-sol":
            raise LauncherError(f"no launcher named {name!r}")
        return Launcher("gpt-sol", ("codex", "--model", "gpt-6.2-sol"), "gpt-6.2-sol")


def _ctx(seen: list):
    def reviewer_launch(command, task, model):
        seen.append((list(command), task, model))
        return lambda brief: (None, model)

    return cli.Context(
        repo=SimpleNamespace(head_commit=lambda: "a" * 40),
        runner=object(),
        sandbox=object(),
        store=object(),
        author_sandbox=object(),
        reviewer_launch=reviewer_launch,
        launchers=_Catalog(),
    )


def _run(argv, seen):
    return cli.main(["strength-review", "--task", "p1", "--run", *argv], context=_ctx(seen))


def test_the_launcher_supplies_command_and_model(monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(
        cli,
        "run_strength_review",
        lambda *a, **k: SimpleNamespace(
            proposal_id="p1", green=True, author_vendor="anthropic", reviewer_vendor="openai"
        ),
    )
    assert _run(["--launcher", "gpt-sol"], seen) == 0
    assert seen == [(["codex", "--model", "gpt-6.2-sol"], "p1", "gpt-6.2-sol")]


def test_an_unknown_launcher_is_refused_before_anything_runs(capsys) -> None:
    seen: list = []
    assert _run(["--launcher", "nope"], seen) == 2
    assert seen == [] and "refused" in capsys.readouterr().err


def test_a_label_contradicting_the_launcher_is_refused(capsys) -> None:
    seen: list = []
    assert _run(["--launcher", "gpt-sol", "--reviewer-model", "claude-opus-5"], seen) == 2
    assert seen == []


def test_without_a_launcher_the_label_is_not_recorded(monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(
        cli,
        "run_strength_review",
        lambda *a, **k: SimpleNamespace(
            proposal_id="p1", green=True, author_vendor="a", reviewer_vendor="b"
        ),
    )
    _run(["--reviewer-model", "gpt-6.2-sol", "--", "agent"], seen)
    assert seen == [(["agent"], "p1", "")]
