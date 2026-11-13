"""criteria.yaml validation (Q27, Q44)."""

import pytest

from cheeseless.domain.contract import (
    Contract,
    canonical_contract_bytes,
    contract_data,
    parse_contract,
)
from cheeseless.domain.task import Criterion

GOOD = {
    "task": "toy",
    "build": [["python", "-m", "compileall", "src"]],
    "run": ["python", "-m", "toy"],
    "env": {"PYTHONPATH": "src"},
    "criteria": [{"id": "normalize", "description": "CLI normalises input"}],
}


def _with(**changes: object) -> dict[str, object]:
    return {**GOOD, **changes}


def test_parses_a_valid_contract() -> None:
    contract = parse_contract(GOOD)
    assert contract.lineage_id == "toy"
    assert contract.run == ("python", "-m", "toy")
    assert contract.build == (("python", "-m", "compileall", "src"),)
    assert contract.env == (("PYTHONPATH", "src"),)
    assert contract.criteria == (Criterion("normalize", "CLI normalises input"),)


def test_build_and_env_are_optional() -> None:
    data = {k: v for k, v in GOOD.items() if k not in ("build", "env")}
    contract = parse_contract(data)
    assert contract.build == ()
    assert contract.env == ()


@pytest.mark.parametrize(
    "data",
    [
        [],
        _with(extra=1),
        {k: v for k, v in GOOD.items() if k != "run"},
        _with(criteria={"id": "x"}),
        _with(criteria=[]),
        _with(criteria=[{"id": "x"}]),
        _with(criteria=[{"id": "x", "description": "d", "tests": ["t"]}]),
        _with(criteria=[{"id": "x", "description": "see test_a.py::test_b"}]),
        _with(criteria=[{"id": "x", "description": "a"}, {"id": "x", "description": "b"}]),
        _with(build="make"),
        _with(build=[[]]),
        _with(build=[["ok"]] * 9),
        _with(run=[]),
        _with(run="python -m toy"),
        _with(run=["python", 1]),
        _with(run=["a" * 257]),
        _with(run=["bad\x00arg"]),
        _with(env=["PYTHONPATH"]),
        _with(env={"lower": "x"}),
        _with(env={"PATH": "/evil"}),
        _with(env={"LD_PRELOAD": "x.so"}),
        _with(env={"PYTEST_ADDOPTS": "-p evil"}),
        _with(env={"CHEESELESS_KEY": "x"}),
        _with(env={"FOO": ""}),
        _with(env={"FOO": 1}),
        _with(task="Bad Name"),
    ],
)
def test_refuses_invalid_contracts(data: object) -> None:
    with pytest.raises(ValueError):
        parse_contract(data)


def test_direct_construction_is_validated() -> None:
    with pytest.raises(ValueError):
        Contract("toy", (), ("python",))
    with pytest.raises(ValueError):
        Contract("toy", (Criterion("a", "b"),), ("python",), env=(("A", "1"), ("A", "2")))


def test_contract_data_round_trips() -> None:
    contract = parse_contract(
        {
            "task": "toy",
            "build": [["python3", "-c", "pass"]],
            "run": ["python3", "-m", "toy"],
            "env": {"PYTHONPATH": "src", "A": "1"},
            "criteria": [{"id": "normalize", "description": "CLI normalises"}],
        }
    )
    assert parse_contract(contract_data(contract)) == contract


def test_canonical_bytes_ignore_input_order() -> None:
    base = {"task": "toy", "run": ["x"], "criteria": [{"id": "a", "description": "d"}]}
    one = parse_contract({**base, "env": {"B": "2", "A": "1"}})
    two = parse_contract({**base, "env": {"A": "1", "B": "2"}})
    assert canonical_contract_bytes(one) == canonical_contract_bytes(two)
    assert canonical_contract_bytes(one) != canonical_contract_bytes(parse_contract(base))
