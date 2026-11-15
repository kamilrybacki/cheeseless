"""The demo's cheeseless mode runs every tactic to a conclusive tri-state outcome."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from demo.run_demo import BLOCKED, GAMED, INCONCLUSIVE, TACTIC_IDS, run_cheeseless_mode
from tests.requirements import needs
from tests.toy import CANARY

pytestmark = needs(
    os.access("/usr/bin/bwrap", os.X_OK) and os.access("/usr/bin/python3", os.X_OK),
    "needs bubblewrap and /usr/bin/python3",
)


def test_every_tactic_is_conclusive_and_blocked_with_structured_evidence() -> None:
    work = Path(tempfile.mkdtemp(prefix="demo-test-"))
    try:
        result = run_cheeseless_mode(work, TACTIC_IDS, emit=lambda _line: None)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    blob = json.dumps(result)
    assert CANARY not in blob
    assert result["control"]["succeeded"] is True
    assert len(result["tactics"]) == len(TACTIC_IDS)
    by_id = {tactic["id"]: tactic for tactic in result["tactics"]}
    assert set(by_id) == set(TACTIC_IDS)
    for tactic in result["tactics"]:
        assert tactic["status"] in {GAMED, BLOCKED, INCONCLUSIVE}
        assert tactic["status"] != INCONCLUSIVE, (tactic["id"], tactic.get("detail"))
        if tactic["status"] == BLOCKED:
            assert tactic["evidence"], tactic["id"]
            assert tactic["detail"], tactic["id"]
    assert by_id["oracle-bruteforce"]["evidence"]["probes"], "bruteforce needs probe detail"
