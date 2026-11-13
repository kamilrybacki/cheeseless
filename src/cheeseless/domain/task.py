"""A frozen task: its freeze digest, budget lineage and public criteria (Q27, I5)."""

from dataclasses import dataclass

from cheeseless.domain import ids

DESCRIPTION_LIMIT = 500


@dataclass(frozen=True, slots=True)
class Criterion:
    """A public acceptance criterion. The description is shown to the agent."""

    id: str
    description: str

    def __post_init__(self) -> None:
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
