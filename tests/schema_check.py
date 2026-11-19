"""A dependency-free checker for the JSON Schema subset ``docs/schemas`` uses.

The project has no ``jsonschema`` dependency, and the dev-event schema is closed on purpose,
so the checker supports exactly the keywords that schema is written with and refuses any
other keyword. A test that passes here means every line satisfies the schema as written.
"""

from __future__ import annotations

import re
from typing import Any

_SUPPORTED = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "const",
        "enum",
        "required",
        "properties",
        "additionalProperties",
        "pattern",
        "minimum",
        "maximum",
        "oneOf",
        "anyOf",
        "not",
    }
)

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "string": (str,),
    "integer": (int,),
}


class UnsupportedKeyword(ValueError):
    """The schema uses a keyword this checker does not implement."""


def violations(schema: dict[str, Any], value: Any, path: str = "$") -> list[str]:
    """Every way ``value`` breaks ``schema``; an empty list means it is valid."""
    unknown = set(schema) - _SUPPORTED
    if unknown:
        raise UnsupportedKeyword(sorted(unknown))
    problems: list[str] = []
    if "const" in schema and value != schema["const"]:
        problems.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: {value!r} is not in the enum")
    if "type" in schema and not _is_type(value, schema["type"]):
        problems.append(f"{path}: expected {schema['type']}")
    if "pattern" in schema and isinstance(value, str):
        if re.fullmatch(schema["pattern"], value) is None:
            problems.append(f"{path}: {value!r} does not match the pattern")
    if isinstance(value, int) and not isinstance(value, bool):
        problems.extend(_bounds(schema, value, path))
    if isinstance(value, dict):
        problems.extend(_object(schema, value, path))
    problems.extend(_combinators(schema, value, path))
    return problems


def _is_type(value: Any, name: str) -> bool:
    if name == "integer" and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[name])


def _bounds(schema: dict[str, Any], value: int, path: str) -> list[str]:
    problems: list[str] = []
    if "minimum" in schema and value < schema["minimum"]:
        problems.append(f"{path}: {value} is below {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        problems.append(f"{path}: {value} is above {schema['maximum']}")
    return problems


def _object(schema: dict[str, Any], value: dict[str, Any], path: str) -> list[str]:
    problems: list[str] = []
    for key in schema.get("required", []):
        if key not in value:
            problems.append(f"{path}: missing {key!r}")
    properties: dict[str, Any] = schema.get("properties", {})
    for key, item in value.items():
        if key in properties:
            problems.extend(violations(properties[key], item, f"{path}.{key}"))
        elif schema.get("additionalProperties") is False:
            problems.append(f"{path}: unexpected field {key!r}")
    return problems


def _combinators(schema: dict[str, Any], value: Any, path: str) -> list[str]:
    problems: list[str] = []
    if "oneOf" in schema:
        matches = [sub for sub in schema["oneOf"] if not violations(sub, value, path)]
        if len(matches) != 1:
            problems.append(f"{path}: matches {len(matches)} oneOf branches, expected 1")
    if "anyOf" in schema and all(violations(sub, value, path) for sub in schema["anyOf"]):
        problems.append(f"{path}: matches no anyOf branch")
    if "not" in schema and not violations(schema["not"], value, path):
        problems.append(f"{path}: matches a forbidden schema")
    return problems
