"""A frozen task: its freeze digest, budget lineage and public criteria (Q27, I5)."""

import re
from dataclasses import dataclass

from cheeseless.domain import ids

DESCRIPTION_LIMIT = 500
SPEC_LIMIT = 64 * 1024
EXAMPLE_TEXT_LIMIT = 2000
EXAMPLES_PER_CRITERION = 20
_NODE_ID = re.compile(r"\.py::")


def check_public_text(text: str, limit: int, what: str) -> None:
    """Public spec/example text: bounded and never naming a test node id (Q44)."""
    if len(text) > limit:
        raise ValueError(f"{what} exceeds {limit} characters")
    if _NODE_ID.search(text):
        raise ValueError(f"{what} may not name test node ids (Q44)")


@dataclass(frozen=True, slots=True)
class Example:
    """Public text input and expected output; never an executable test."""

    input: str
    expected_output: str

    def __post_init__(self) -> None:
        if not isinstance(self.input, str) or not isinstance(self.expected_output, str):
            raise ValueError("examples input and expected_output must be strings")
        check_public_text(self.input, EXAMPLE_TEXT_LIMIT, "example input")
        check_public_text(self.expected_output, EXAMPLE_TEXT_LIMIT, "example expected_output")


@dataclass(frozen=True, slots=True)
class Criterion:
    """A public acceptance criterion. The description is shown to the agent."""

    id: str
    description: str
    examples: tuple[Example, ...] | None = None

    def __post_init__(self) -> None:
        if self.examples is not None and (
            not isinstance(self.examples, tuple)
            or not all(isinstance(e, Example) for e in self.examples)
        ):
            raise ValueError("examples must be a tuple of Example values")
        if self.examples is not None and len(self.examples) > EXAMPLES_PER_CRITERION:
            raise ValueError(f"at most {EXAMPLES_PER_CRITERION} examples per criterion")
        ids.criterion_id(self.id)
        d = self.description
        if not isinstance(d, str) or not d.strip() or len(d) > DESCRIPTION_LIMIT:
            raise ValueError("criterion description must be 1-500 characters")


@dataclass(frozen=True, slots=True)
class Task:
    """``task_id`` is the freeze digest; ``lineage_id`` keys the run budget (I5)."""

    task_id: str
    lineage_id: str
    criteria: tuple[Criterion, ...]

    def __post_init__(self) -> None:
        ids.sha256_hex(self.task_id, "task id")
        ids.lineage_id(self.lineage_id)
        if not isinstance(self.criteria, tuple) or not self.criteria:
            raise ValueError("a task needs at least one criterion")
        if not all(isinstance(c, Criterion) for c in self.criteria):
            raise ValueError("criteria must be Criterion values")
        if len({c.id for c in self.criteria}) != len(self.criteria):
            raise ValueError("duplicate criterion id")

    def criterion(self, criterion_id: str) -> Criterion:
        for c in self.criteria:
            if c.id == criterion_id:
                return c
        raise KeyError(criterion_id)

    @property
    def criterion_ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.criteria)
