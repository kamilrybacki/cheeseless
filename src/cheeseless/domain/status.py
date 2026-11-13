"""Verdict values (D1). There is deliberately no UNSPECIFIED member: fail closed."""

import enum


class Status(enum.Enum):
    PASS = "pass"  # noqa: S105 - a verdict, not a credential
    FAIL = "fail"
    BLOCKED = "blocked"
