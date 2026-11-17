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


def test_spec_and_examples_are_bound_to_canonical_bytes() -> None:
    from cheeseless.domain.freeze import freeze_digest

    data = _with(
        criteria=[
            {
                "id": "normalize",
                "description": "CLI normalises input",
                "examples": [{"input": "Hello\n", "expected_output": "hello\n"}],
            }
        ]
    )
    old = canonical_contract_bytes(parse_contract(GOOD))
    contract = parse_contract(data, spec="# Public spec\n")
    assert contract.spec == "# Public spec\n"
    assert contract.criteria[0].examples[0].input == "Hello\n"
    assert contract.criteria[0].examples[0].expected_output == "hello\n"
    assert parse_contract(contract_data(contract), spec=contract.spec) == contract
    encodings = [
        old,
        canonical_contract_bytes(parse_contract(data)),
        canonical_contract_bytes(contract),
        canonical_contract_bytes(parse_contract(data, spec="")),
        canonical_contract_bytes(parse_contract(data, spec="changed")),
    ]
    assert len(set(encodings)) == len(encodings)
    assert len({freeze_digest("1" * 40, "2" * 40, b) for b in encodings}) == len(encodings)


@pytest.mark.parametrize(
    "examples",
    [
        None,
        {},
        "text",
        [None],
        [{}],
        [{"input": "x"}],
        [{"input": "x", "expected_output": 1}],
        [{"input": [], "expected_output": "x"}],
        [{"input": "x", "expected_output": "y", "hidden": "z"}],
    ],
)
def test_rejects_malformed_examples(examples: object) -> None:
    with pytest.raises(ValueError, match="examples"):
        parse_contract(
            _with(
                criteria=[
                    {
                        "id": "normalize",
                        "description": "d",
                        "examples": examples,
                    }
                ]
            )
        )


def test_empty_examples_and_empty_text_are_supported() -> None:
    for examples in ([], [{"input": "", "expected_output": ""}]):
        contract = parse_contract(
            _with(
                criteria=[
                    {
                        "id": "normalize",
                        "description": "d",
                        "examples": examples,
                    }
                ]
            )
        )
        assert parse_contract(contract_data(contract)) == contract


def test_explicit_empty_examples_are_in_digest() -> None:
    absent = parse_contract(GOOD)
    present = parse_contract(_with(criteria=[{**GOOD["criteria"][0], "examples": []}]))
    assert canonical_contract_bytes(absent) != canonical_contract_bytes(present)


def _example(text: str) -> dict[str, object]:
    return {"input": text, "expected_output": "ok"}


def _criterion_with(examples: list[object]) -> dict[str, object]:
    return {"id": "normalize", "description": "CLI normalises input", "examples": examples}


@pytest.mark.parametrize(
    "examples",
    [
        [_example("see tests/test_x.py::test_hidden")],
        [{"input": "x", "expected_output": "acceptance/t.py::test_case"}],
        [_example("x" * 2001)],
        [_example("x")] * 21,
    ],
)
def test_examples_are_capped_and_may_not_name_node_ids(examples: list[object]) -> None:
    with pytest.raises(ValueError):
        parse_contract(_with(criteria=[_criterion_with(examples)]))


def test_examples_may_use_double_colons_outside_node_ids() -> None:
    contract = parse_contract(_with(criteria=[_criterion_with([_example("std::vec")])]))
    assert contract.criteria[0].examples is not None


@pytest.mark.parametrize("spec", ["run tests/x.py::test_hidden", "x" * (64 * 1024 + 1)])
def test_spec_is_capped_and_may_not_name_node_ids(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_contract(GOOD, spec=spec)
