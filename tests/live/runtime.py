"""Wires the real adapters for a live run. Reads env var NAMES from ``config`` only."""

from __future__ import annotations

import secrets
import time
from collections.abc import Mapping

from tests.live.config import load_config
from tests.live.github_client import UrllibGitHubClient
from tests.live.model import Ctx
from tests.live.seeding_port import CheeselessSeeder
from tests.live.tokens import AppTokenMinter


def build_ctx(env: Mapping[str, str]) -> tuple[Ctx, AppTokenMinter]:
    config = load_config(env)
    client = UrllibGitHubClient()
    minter = AppTokenMinter(
        client,
        app_id=config.app_id,
        key_file=config.key_file,
        installations=config.installations,
    )
    ctx = Ctx(
        config=config,
        client=client,
        minter=minter,
        seeder=CheeselessSeeder(),
        sleep=time.sleep,
        random_hex=lambda: secrets.token_hex(4),
    )
    return ctx, minter
