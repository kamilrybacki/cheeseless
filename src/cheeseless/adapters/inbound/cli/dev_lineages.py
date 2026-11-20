"""Host-only ``dev-lineages`` output: one JSON line listing every lineage's stage.

Read-only by construction: the only port it touches is ``LineageListing``. Nothing here is
exposed on MCP or UDS. Values are limited to lineage ids, stage enums and UTC times; a
store error prints a fixed line, never a message or a path.
"""

from __future__ import annotations

from typing import TextIO

from cheeseless.app.dev_lineages import list_lineages
from cheeseless.domain.dev_lineages import STORE_ERROR_LINE, encode_listing
from cheeseless.ports.dev_observe import LineageListing
from cheeseless.ports.store import StoreError

EXIT_OK = 0
EXIT_STORE_ERROR = 1
EXIT_INTERRUPTED = 130


def run_dev_lineages(port: LineageListing, *, out: TextIO) -> int:
    """Print the listing and return the exit code.

    0 for a listing (an empty one included), 1 with the fixed store-error line when the
    store cannot be read. A closed pipe exits 0 and an interrupt exits 130, both silently.
    """
    try:
        try:
            line = encode_listing(list_lineages(port))
        except (StoreError, OSError, ValueError):
            return _emit(out, STORE_ERROR_LINE, EXIT_STORE_ERROR)
        return _emit(out, line, EXIT_OK)
    except BrokenPipeError:
        return EXIT_OK
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED


def _emit(out: TextIO, line: str, code: int) -> int:
    out.write(line + "\n")
    out.flush()
    return code
