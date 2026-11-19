"""``cheeseless dev-events``: the host-only, read-only dev-events command (S3).

The composition root for the observer. It builds an :class:`ObserveStore` directly, never
``build_context``: constructing a context creates store directories, a sandbox and a
runner, none of which a read-only view may touch. Only the ``DevObservation`` port reaches
the inbound adapter, so the adapter stays independent of the outbound store.

Exit codes: 0 normal, 1 a fixed error line was printed, 2 usage (or no repository), 3 no
such lineage (``--once``), 130 interrupted.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from cheeseless.adapters.inbound.cli.dev_events import run_dev_events
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import store_root
from cheeseless.adapters.outbound.store.observe_store import ObserveStore
from cheeseless.bootstrap import _state_home
from cheeseless.domain import ids
from cheeseless.ports.repository import RepositoryError

USAGE_EXIT = 2
MAX_INTERVAL_SECONDS = 60.0


def run_dev_events_command(args: argparse.Namespace) -> int:
    try:
        lineage = ids.lineage_id(args.lineage)
    except ValueError as exc:
        print(f"cheeseless dev-events: {exc}", file=sys.stderr)
        return USAGE_EXIT
    if not 0 < args.interval <= MAX_INTERVAL_SECONDS:  # also rejects nan
        print(
            "cheeseless dev-events: --interval must be above 0 and at most "
            f"{MAX_INTERVAL_SECONDS:g}",
            file=sys.stderr,
        )
        return USAGE_EXIT
    try:
        repo = GitRepository.discover(Path.cwd())
    except RepositoryError:
        print("cheeseless dev-events: not inside a git repository", file=sys.stderr)
        return USAGE_EXIT
    state_home = _state_home(getattr(args, "state_dir", None))
    store = ObserveStore(store_root(repo.common_dir(), state_home))
    return run_dev_events(
        store,
        lineage,
        follow=bool(args.follow),
        interval=float(args.interval),
        out=sys.stdout,
        now=lambda: datetime.now(UTC),
        sleep=time.sleep,
    )
