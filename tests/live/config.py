"""Live-suite configuration. It reads environment variable NAMES; values never get printed."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tests.live.github_client import CANARY_PREFIX, Token

ENABLE = "CHEESELESS_LIVE_GITHUB"
APP_ID = "CHEESELESS_LIVE_APP_ID"
APP_KEY_FILE = "CHEESELESS_LIVE_APP_KEY_FILE"
PUSHER_TOKEN = "CHEESELESS_LIVE_PUSHER_TOKEN"  # noqa: S105 - an env var name
ADMIN_TOKEN = "CHEESELESS_LIVE_ADMIN_TOKEN"  # noqa: S105 - an env var name
SAME_REPO = "CHEESELESS_LIVE_SAME_REPO"
LEDGER_REPO = "CHEESELESS_LIVE_LEDGER_REPO"
JUDGED_REPO = "CHEESELESS_LIVE_JUDGED_REPO"
FRESH_REPO = "CHEESELESS_LIVE_FRESH_REPO"
CANARY_SECRET = "CHEESELESS_LIVE_CANARY_SECRET"  # noqa: S105 - an env var name
LEDGER_INSTALLATION = "CHEESELESS_LIVE_LEDGER_INSTALLATION_ID"
JUDGED_INSTALLATION = "CHEESELESS_LIVE_JUDGED_INSTALLATION_ID"

REQUIRED = (
    APP_ID,
    APP_KEY_FILE,
    PUSHER_TOKEN,
    ADMIN_TOKEN,
    LEDGER_REPO,
    JUDGED_REPO,
    FRESH_REPO,
    CANARY_SECRET,
)
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}")
_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


class ConfigError(Exception):
    """Configuration is incomplete or malformed. The message lists variable names only."""


@dataclass(frozen=True)
class LiveConfig:
    app_id: str
    key_file: Path
    pusher_token: Token
    admin_token: Token
    ledger_repo: str
    judged_repo: str
    fresh_repo: str
    canary_secret: str
    installations: Mapping[str, int]
    same_repo: str = ""

    @property
    def ledger_name(self) -> str:
        return self.ledger_repo.split("/", 1)[1]


def enabled(env: Mapping[str, str]) -> bool:
    return env.get(ENABLE) == "1"


def _installation(env: Mapping[str, str], name: str) -> int | None:
    raw = env.get(name, "")
    if not raw:
        return None
    if not raw.isdigit():
        raise ConfigError(f"{name} must be a number")
    return int(raw)


def load_config(env: Mapping[str, str]) -> LiveConfig:
    missing = [name for name in REQUIRED if not env.get(name)]
    if missing:
        raise ConfigError("missing environment variables: " + ", ".join(missing))
    if not env[APP_ID].isdigit():
        raise ConfigError(f"{APP_ID} must be a number")
    repos = {n: env[n] for n in (LEDGER_REPO, JUDGED_REPO, FRESH_REPO)}
    if env.get(SAME_REPO):
        repos[SAME_REPO] = env[SAME_REPO]
    for name, value in repos.items():
        if not _REPO.fullmatch(value):
            raise ConfigError(f"{name} must look like OWNER/NAME")
        if not value.split("/", 1)[1].startswith(CANARY_PREFIX):
            raise ConfigError(f"{name} must name a disposable {CANARY_PREFIX}* repository")
    if len(set(repos.values())) != len(repos):
        raise ConfigError("the canary repositories must all be different repositories")
    if not _NAME.fullmatch(env[CANARY_SECRET]):
        raise ConfigError(f"{CANARY_SECRET} must be an upper-case secret NAME")
    found = {
        repos[LEDGER_REPO if n == LEDGER_INSTALLATION else JUDGED_REPO]: _installation(env, n)
        for n in (LEDGER_INSTALLATION, JUDGED_INSTALLATION)
    }
    return LiveConfig(
        app_id=env[APP_ID],
        key_file=Path(env[APP_KEY_FILE]),
        pusher_token=Token(env[PUSHER_TOKEN], "pusher"),
        admin_token=Token(env[ADMIN_TOKEN], "admin"),
        ledger_repo=env[LEDGER_REPO],
        judged_repo=env[JUDGED_REPO],
        fresh_repo=env[FRESH_REPO],
        canary_secret=env[CANARY_SECRET],
        installations={repo: i for repo, i in found.items() if i is not None},
        same_repo=env.get(SAME_REPO, ""),
    )
