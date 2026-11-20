"""``cheeseless dev-lineages``: the host-only, read-only lineage listing.

The composition root for the listing. Like ``dev-events`` it builds an
:class:`ObserveStore` directly, never ``build_context``, so nothing is created on disk.
Only the ``LineageListing`` port reaches the inbound adapter.

Exit codes: 0 normal (an empty list included), 1 the fixed store-error line was printed,
2 usage (or no repository), 130 interrupted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cheeseless.adapters.inbound.cli.dev_lineages import run_dev_lineages
from cheeseless.adapters.outbound.git.repository import GitRepository
from cheeseless.adapters.outbound.store.file_store import store_root
from cheeseless.adapters.outbound.store.observe_store import ObserveStore
from cheeseless.bootstrap import _state_home
from cheeseless.ports.repository import RepositoryError

USAGE_EXIT = 2


def run_dev_lineages_command(args: argparse.Namespace) -> int:
    try:
        repo = GitRepository.discover(Path.cwd())
    except RepositoryError:
        print("cheeseless dev-lineages: not inside a git repository", file=sys.stderr)
        return USAGE_EXIT
    state_home = _state_home(getattr(args, "state_dir", None))
    store = ObserveStore(store_root(repo.common_dir(), state_home))
    return run_dev_lineages(store, out=sys.stdout)
