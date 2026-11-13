"""Trusted test runner port (D4, D17)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cheeseless.domain.evaluation import SuiteRun


@dataclass(frozen=True, slots=True)
class RunnerOutput:
    suite: SuiteRun
    # Combined pytest output. Operator-only after hidden input.
    output: str = ""


class SuiteRunner(Protocol):
    def run(
        self,
        root: Path,
        paths: Sequence[str],
        *,
        selected: Mapping[str, str] | None,
        launcher: Sequence[str],
        run: Sequence[str],
        timeout: float,
    ) -> RunnerOutput:
        """Run the tests at ``paths`` under ``root``.

        ``selected`` restricts the run to those node ids (None: all collected).
        Tests reach the candidate only through ``launcher + run``.
        """
        ...
