"""Generate (or check) the cheeseless.v1 Python classes from proto/.

Output lives in cheeseless.adapters.inbound.proto so generated classes can only be
reached from adapters (ADR 0004). protoc emits absolute `cheeseless.v1` imports that
would collide with our own package, so they are rewritten to relative imports.
"""

import argparse
import re
import sys
import tempfile
from pathlib import Path

from grpc_tools import protoc

ROOT = Path(__file__).resolve().parents[1]
PROTO_DIR = ROOT / "proto"
OUT_DIR = ROOT / "src" / "cheeseless" / "adapters" / "inbound" / "proto"
INIT = '"""Generated cheeseless.v1 proto classes (inbound adapters only; do not edit)."""\n'
ABS_IMPORT = re.compile(r"^from cheeseless\.v1 import (\w+) as ", re.MULTILINE)


def _generate(dest: Path) -> None:
    sources = sorted(str(p) for p in (PROTO_DIR / "cheeseless" / "v1").glob("*.proto"))
    args = ["protoc", f"-I{PROTO_DIR}", f"--python_out={dest}", f"--pyi_out={dest}", *sources]
    if protoc.main(args) != 0:
        raise SystemExit("protoc failed")
    flat = dest / "cheeseless" / "v1"
    for path in sorted(flat.iterdir()):
        text = ABS_IMPORT.sub(r"from . import \1 as ", path.read_text())
        (dest / path.name).write_text(text)
    for stale in (dest / "cheeseless").rglob("*"):
        if stale.is_file():
            stale.unlink()
    for directory in sorted((dest / "cheeseless").rglob("*"), reverse=True):
        directory.rmdir()
    (dest / "cheeseless").rmdir()
    (dest / "__init__.py").write_text(INIT)


def _snapshot(directory: Path) -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(directory.glob("*")) if p.is_file()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if committed output differs")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp)
        _generate(fresh)
        if args.check:
            if _snapshot(fresh) != _snapshot(OUT_DIR):
                print("generated proto is stale: run `uv run python scripts/gen_proto.py`")
                return 1
            return 0
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for old in OUT_DIR.glob("*"):
            if old.is_file():
                old.unlink()
        for name, text in _snapshot(fresh).items():
            (OUT_DIR / name).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
