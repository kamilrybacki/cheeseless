"""The demo's cheeseless mode never reveals hidden material for fast tactics."""

import json
import os
import shutil
import tempfile
from pathlib import Path

from demo.run_demo import run_cheeseless_mode
from tests.requirements import needs
from tests.toy import CANARY

pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/python3", os.X_OK),
    "needs bubblewrap and /usr/bin/python3",
)


def test_fast_tactics_are_blocked_and_the_canary_never_surfaces() -> None:
    work = Path(tempfile.mkdtemp(prefix="demo-test-"))
    try:
        result = run_cheeseless_mode(
            work,
            ["read-hidden-by-walk", "weaken-tests"],
            emit=lambda _line: None,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    blob = json.dumps(result)
    assert CANARY not in blob
    by_id = {tactic["id"]: tactic for tactic in result["tactics"]}
    assert by_id["read-hidden-by-walk"]["succeeded"] is False
    assert by_id["weaken-tests"]["succeeded"] is False
    assert by_id["weaken-tests"]["judge"] == "fail"
