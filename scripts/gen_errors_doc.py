"""Generate (or check) docs/errors.md from the ErrorCode enum in the proto."""

import argparse
import sys
from pathlib import Path

from cheeseless.adapters.inbound.proto import error_pb2
from cheeseless.domain.errors import CATALOGUE, PRE_HIDDEN_STAGE_CODES, ErrorCode

OUT = Path(__file__).resolve().parents[1] / "docs" / "errors.md"
PREFIX = "ERROR_CODE_"

HEADER = """# Error codes

Generated from `proto/cheeseless/v1/error.proto` by `scripts/gen_errors_doc.py`; do not
edit. Every agent-facing error carries one of these codes and a `docs` anchor pointing here
(ADR 0008). Pre-hidden stages may attach a DLP-scanned stderr tail of about 4 KB and
cost no run budget (Q51, Q52).
"""


def render() -> str:
    parts = [HEADER]
    for value in error_pb2.ErrorCode.DESCRIPTOR.values:
        if value.number == 0:
            continue
        code = ErrorCode[value.name.removeprefix(PREFIX)]
        entry = CATALOGUE[code]
        stage = "yes" if code in PRE_HIDDEN_STAGE_CODES else "no"
        parts.append(
            f"### `{code.name}`\n\n"
            f"{entry.reason}\n\n"
            f"- **What to do:** {entry.hint}\n"
            f"- **Retryable:** {'yes' if entry.retryable else 'no'}\n"
            f"- **Stderr tail (pre-hidden stage):** {stage}\n"
            f"- **Wire:** `{value.name}` = {value.number}\n"
        )
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            print("docs/errors.md is stale: run `uv run python scripts/gen_errors_doc.py`")
            return 1
        return 0
    OUT.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
