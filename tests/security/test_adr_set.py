"""The ADR set stays honest: indexed, short, templated, and matching what the code does.

`AGENTS.md` fixes these rules, and nothing checked them. An ADR that drifts from the code is
worse than no ADR, because it reads as a guarantee nobody is testing.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
from pathlib import Path

import pytest

ADR_DIR = Path(__file__).resolve().parents[2] / "docs" / "adr"


def _adrs() -> list[Path]:
    return sorted(p for p in ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"))


def test_there_are_adrs_to_check() -> None:
    assert _adrs(), "the ADR directory should not be empty"


@pytest.mark.parametrize("path", _adrs(), ids=lambda p: p.stem)
def test_every_adr_is_indexed(path: Path) -> None:
    """An ADR nobody can find is not a decision the project made."""
    index = (ADR_DIR / "README.md").read_text()
    assert f"({path.name})" in index, f"{path.name} is missing from docs/adr/README.md"


@pytest.mark.parametrize("path", _adrs(), ids=lambda p: p.stem)
def test_every_adr_fits_the_length_limit(path: Path) -> None:
    """`AGENTS.md`: an ADR fits in 40 lines. Counting a missing final newline hid one."""
    text = path.read_text()
    lines = text.splitlines()
    assert len(lines) <= 40, f"{path.name} is {len(lines)} lines"
    assert text.endswith("\n"), f"{path.name} has no trailing newline"


@pytest.mark.parametrize("path", _adrs(), ids=lambda p: p.stem)
def test_every_adr_has_the_template_sections(path: Path) -> None:
    text = path.read_text()
    for section in ("## Context", "## Options", "## Decision", "## Consequences"):
        assert section in text, f"{path.name} is missing {section}"
    # The existing ADRs use variations ("accepted; point 2 superseded by ADR-0014"), so this
    # checks the word and lets the qualifier follow, rather than rewriting accepted history.
    assert re.search(r"^status: (proposed|accepted|superseded)", text, re.M), (
        f"{path.name} has no status line"
    )
    assert re.search(r"^date: \d{4}-\d{2}-\d{2}$", text, re.M), f"{path.name} has no date"
    assert "reviewers:" in text, f"{path.name} does not record its reviewers"


def test_adr_0016_names_the_bounds_that_actually_exist() -> None:
    """The ADR says auto-freeze is bounded three ways. Each one must be in the code.

    Written because the previous ADR compared itself against the wrong thing and nobody
    noticed: the text promised bounds and only the tests could have said otherwise.
    """
    from cheeseless.app import authoring
    from cheeseless.domain.authoring import StrengthReview

    decision = (ADR_DIR / "0016-strength-review-substitutes-human-approval.md").read_text()

    # 1. Opt-in per project: the gate reads a stored setting before it looks at any review.
    assert "auto_freeze_enabled" in decision
    # The check lives in `approve_freeze`'s unattended branch so that `--approve --auto` and
    # `freeze --yes` are held to it alike, and `auto_approve_freeze` is the only caller that
    # turns that branch on.
    gate = inspect.getsource(authoring._unattended_problem)  # noqa: SLF001
    assert "auto_freeze_enabled" in gate
    assert "_unattended_problem" in inspect.getsource(authoring.approve_freeze)
    assert "unattended=True" in inspect.getsource(authoring.auto_approve_freeze)

    # 2. Only a review the host ran and read counts.
    assert "observed" in decision
    assert "observed" in {f.name for f in dataclasses.fields(StrengthReview)}

    # 3. The vendor comes from the launch, not a flag.
    source = Path(authoring.__file__).read_text()
    assert "launched_model" in source or "launch(" in source
    assert "reviewer_model=launched_model" in source
