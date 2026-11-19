"""``cheeseless dev-events``: the host-only, read-only dev-events command (S3).

The composition root for the observer. It builds an :class:`ObserveStore` directly, never
``build_context``: constructing a context creates store directories, a sandbox and a
runner, none of which a read-only view may touch. Only the ``DevObservation`` port reaches
the inbound adapter, so the adapter stays independent of the outbound store.

Exit codes: 0 normal, 1 a fixed error line was printed (``--once``), 2 usage.
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

USAGE_EXIT = 2


def run_dev_events_command(args: argparse.Namespace) -> int:
    try:
        lineage = ids.lineage_id(args.lineage)
    except ValueError as exc:
        print(f"cheeseless dev-events: {exc}", file=sys.stderr)
        return USAGE_EXIT
    if args.interval <= 0:
        print("cheeseless dev-events: --interval must be greater than zero", file=sys.stderr)
        return USAGE_EXIT
    repo = GitRepository.discover(Path.cwd())
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
