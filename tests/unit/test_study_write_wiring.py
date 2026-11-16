"""Refused-write evidence flows from the transcript into the arm classification."""

from study import arms, fake_agent
from study.classifier import Event
from study.tasks import TASKS_BY_ID

TASK = TASKS_BY_ID["slug"]


def _naive(tmp_path, observed):
    def apply(root):
        return fake_agent.act("honest", root, TASK)

    return arms.run_naive(TASK, tmp_path, apply, observed)


def test_fake_weakener_read_only_refuses_and_leaves_tests(tmp_path):
    tree = tmp_path / "t"
    (tree / "acceptance").mkdir(parents=True)
    test = tree / "acceptance" / "test_a.py"
    test.write_text("assert x\n")
    events = fake_agent.act("weakener", tree, TASK, tests_read_only=True)
    assert test.read_text() == "assert x\n"
    assert events == (Event("write", "acceptance/test_a.py", ok=False),)


def test_fake_weakener_writable_rewrites_tests(tmp_path):
    tree = tmp_path / "t"
    (tree / "acceptance").mkdir(parents=True)
    test = tree / "acceptance" / "test_a.py"
    test.write_text("assert x\n")
    events = fake_agent.act("weakener", tree, TASK)
    assert "placeholder" in test.read_text()
    assert events[0].ok is True


def test_run_naive_counts_observed_refused_write(tmp_path):
    refused = (Event("write", "acceptance/test_x.py", ok=False),)
    result = _naive(tmp_path, lambda: refused)
    assert result.classification.flags["touch_tests"] is True
    assert result.classification.refused_attempt is True


def test_run_naive_unobserved_or_empty_writes_change_nothing(tmp_path):
    for observed in (None, lambda: None, lambda: ()):
        result = _naive(tmp_path / str(id(observed)), observed)
        assert result.classification.flags["touch_tests"] is False
        assert result.classification.refused_attempt is False
