"""List the lineages that have dev state: newest change first, at most MAX_LINEAGES."""

from __future__ import annotations

from cheeseless.app.dev_observe import current_view
from cheeseless.domain.dev_lineages import MAX_LINEAGES, LineageEntry, newest_first
from cheeseless.ports.dev_observe import LineageListing


def list_lineages(port: LineageListing) -> tuple[LineageEntry, ...]:
    """The newest lineages with their stage. A StoreError from any of them propagates.

    A lineage whose state vanished between the listing and the read is skipped and does
    not take one of the slots.
    """
    entries: list[LineageEntry] = []
    for stamp in newest_first(port.lineage_stamps()):
        if len(entries) == MAX_LINEAGES:
            break
        view = current_view(port, stamp.lineage_id)
        if view is not None:
            entries.append(LineageEntry(stamp.lineage_id, view.stage, stamp.updated_at))
    return tuple(entries)
