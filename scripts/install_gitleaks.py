"""Install the pinned official Gitleaks Linux x64 release with a checksum check.

The release audit fails closed unless the scanner reports exactly the pinned
version.  This helper downloads only the official GitHub release asset, verifies
the published SHA-256, extracts the binary and checks ``gitleaks version``.  It
never receives or prints credentials.

Usage::

    python scripts/install_gitleaks.py --dest "$HOME/.local/bin"
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess  # noqa: S404 - runs the just-verified local binary
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

VERSION = "8.30.1"
URL = (
    "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/"
    "gitleaks_8.30.1_linux_x64.tar.gz"
)
SHA256 = "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"


class InstallError(Exception):
    pass


def _download(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - pinned https URL
            return bytes(response.read())
    except OSError as exc:
        raise InstallError("download-failed") from exc


def _verify(data: bytes) -> None:
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise InstallError("checksum-mismatch")


def _extract(archive: bytes, destination: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "gitleaks.tar.gz"
        path.write_bytes(archive)
        try:
            with tarfile.open(path, "r:gz") as handle:
                members = [m for m in handle.getmembers() if m.isfile()]
                selected = [m for m in members if m.name == "gitleaks"]
                if len(selected) != 1:
                    raise InstallError("unexpected-archive")
                source = handle.extractfile(selected[0])
                if source is None:
                    raise InstallError("unexpected-archive")
                destination.parent.mkdir(parents=True, exist_ok=True)
                staged = destination.with_suffix(".tmp")
                with staged.open("wb") as out:
                    shutil.copyfileobj(source, out)
                os.chmod(staged, 0o755)  # noqa: S103 - an executable binary must be executable
                staged.replace(destination)
        except (tarfile.TarError, OSError) as exc:
            raise InstallError("extract-failed") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=Path.home() / ".local" / "bin")
    args = parser.parse_args(argv)
    destination = args.dest.expanduser().resolve() / "gitleaks"
    try:
        archive = _download(URL)
        _verify(archive)
        _extract(archive, destination)
        result = subprocess.run(  # noqa: S603 - fixed argv, just-verified binary
            [str(destination), "version"], capture_output=True, timeout=30, check=False
        )
        if result.returncode != 0 or result.stdout.decode().strip() != VERSION:
            raise InstallError("version-mismatch")
    except InstallError as exc:
        print(f"gitleaks install failed closed: {exc}", file=sys.stderr)
        return 1
    print(f"installed gitleaks {VERSION} (sha256 {SHA256})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
