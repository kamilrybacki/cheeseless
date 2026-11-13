"""cheeseless: a git-native black-box quality gate for coding agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cheeseless")
except PackageNotFoundError:  # pragma: no cover - running from an uninstalled tree
    __version__ = "0.0.0+unknown"
