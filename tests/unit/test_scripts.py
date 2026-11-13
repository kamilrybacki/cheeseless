"""Scripts that keep generated artifacts in sync with proto/."""

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_proto_is_up_to_date() -> None:
    assert _load("gen_proto").main(["--check"]) == 0


def test_errors_doc_is_up_to_date_and_lists_codes() -> None:
    module = _load("gen_errors_doc")
    assert module.main(["--check"]) == 0
    text = (ROOT / "docs" / "errors.md").read_text()
    for code in ("RUN_BUDGET_EXHAUSTED", "RATE_LIMITED", "DLP_BLOCKED"):
        assert f"### `{code}`" in text
    assert "UNSPECIFIED" not in text
