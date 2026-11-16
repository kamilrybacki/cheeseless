"""Agent-facing error model and its trusted text catalogue (ADR 0008, Q50-Q52, D10).

Every string here is a trusted template. Nothing derived from hidden inputs may be
placed in ``reason``, ``hint`` or ``details``; only pre-hidden stages may attach a
stderr tail, which the caller must DLP-scan first.
"""

import enum
import posixpath
from dataclasses import dataclass, field

from cheeseless.domain import ids
from cheeseless.domain.task import DESCRIPTION_LIMIT


class ErrorCode(enum.Enum):
    """Mirrors ``cheeseless.v1.ErrorCode`` without the prefix and without UNSPECIFIED."""

    RUN_BUDGET_EXHAUSTED = "run_budget_exhausted"
    CRITERION_FAILED = "criterion_failed"
    BUILD_FAILED = "build_failed"
    PROMOTE_PATH_REJECTED = "promote_path_rejected"
    SENSITIVE_FILE_NEEDS_APPROVAL = "sensitive_file_needs_approval"
    EGRESS_DOMAIN_DENIED = "egress_domain_denied"
    EGRESS_SERVER_TOOL_DENIED = "egress_server_tool_denied"
    DLP_BLOCKED = "dlp_blocked"
    INFRA_ERROR = "infra_error"
    RATE_LIMITED = "rate_limited"
    IMPORT_FAILED = "import_failed"
    EXECUTABLE_MISSING = "executable_missing"
    STARTUP_CRASH = "startup_crash"
    SMOKE_TEST_FAILED = "smoke_test_failed"
    DISPUTE_DISABLED = "dispute_disabled"
    DISPUTE_CAP_REACHED = "dispute_cap_reached"
    DISPUTE_INVALID = "dispute_invalid"
    DISPUTE_NOT_ALLOWED = "dispute_not_allowed"
    SESSION_DISPUTED = "session_disputed"


# Stages before the first hidden input that may carry a stderr tail (Q51).
PRE_HIDDEN_STAGE_CODES = frozenset(
    {
        ErrorCode.BUILD_FAILED,
        ErrorCode.IMPORT_FAILED,
        ErrorCode.EXECUTABLE_MISSING,
        ErrorCode.STARTUP_CRASH,
        ErrorCode.SMOKE_TEST_FAILED,
    }
)
STDERR_TAIL_LIMIT = 4096
PATH_LIMIT = 256


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    reason: str
    hint: str
    # A later attempt can succeed without operator action.
    retryable: bool


CATALOGUE: dict[ErrorCode, CatalogueEntry] = {
    ErrorCode.RUN_BUDGET_EXHAUSTED: CatalogueEntry(
        "The run budget for this task is used up.",
        "Stop calling gate_run. Ask the operator to grant more runs.",
        False,
    ),
    ErrorCode.CRITERION_FAILED: CatalogueEntry(
        "An acceptance criterion failed.",
        "Re-read the public criterion description and the contract tests, then fix the code.",
        True,
    ),
    ErrorCode.BUILD_FAILED: CatalogueEntry(
        "The project did not build.",
        "Fix the build error shown in the stderr tail. This attempt cost no run budget.",
        True,
    ),
    ErrorCode.PROMOTE_PATH_REJECTED: CatalogueEntry(
        "The change touches a path that may not be promoted.",
        "Keep changes inside src/, tests/, docs/ or README files.",
        False,
    ),
    ErrorCode.SENSITIVE_FILE_NEEDS_APPROVAL: CatalogueEntry(
        "The change touches a sensitive file.",
        "Ask the operator to approve this file, or leave it unchanged.",
        False,
    ),
    ErrorCode.EGRESS_DOMAIN_DENIED: CatalogueEntry(
        "The request went to a host that is not on the allowlist.",
        "Use only allowed hosts. Ask the operator if you need another one.",
        False,
    ),
    ErrorCode.EGRESS_SERVER_TOOL_DENIED: CatalogueEntry(
        "The request asked the model provider to run a server-side tool.",
        "Server-side tools such as web fetch or code execution are disabled in gated sessions.",
        False,
    ),
    ErrorCode.DLP_BLOCKED: CatalogueEntry(
        "Outbound content matched protected material and the session was stopped.",
        "The operator has been alerted. Do not retry.",
        False,
    ),
    ErrorCode.INFRA_ERROR: CatalogueEntry(
        "The gate failed for a reason unrelated to the candidate.",
        "Retry later. If it keeps failing, tell the operator.",
        True,
    ),
    ErrorCode.RATE_LIMITED: CatalogueEntry(
        "Too many attempts before the hidden stage in a short time.",
        "Wait before retrying and fix the reported problem locally first.",
        True,
    ),
    ErrorCode.IMPORT_FAILED: CatalogueEntry(
        "The package could not be imported.",
        "Fix the import error shown in the stderr tail. This attempt cost no run budget.",
        True,
    ),
    ErrorCode.EXECUTABLE_MISSING: CatalogueEntry(
        "The command-line executable named by the contract was not found.",
        "Make sure the project installs the executable described in acceptance/contract/.",
        True,
    ),
    ErrorCode.STARTUP_CRASH: CatalogueEntry(
        "The program crashed on startup.",
        "Fix the crash shown in the stderr tail. This attempt cost no run budget.",
        True,
    ),
    ErrorCode.SMOKE_TEST_FAILED: CatalogueEntry(
        "A public smoke test from acceptance/contract/ failed.",
        "Run the contract tests locally and fix the failure. This attempt cost no run budget.",
        True,
    ),
    ErrorCode.DISPUTE_DISABLED: CatalogueEntry(
        "Disputes are not enabled for this repository.",
        "Keep working on the public criteria, or ask the operator to enable disputes.",
        False,
    ),
    ErrorCode.DISPUTE_CAP_REACHED: CatalogueEntry(
        "The dispute limit for this task lineage is used up.",
        "Do not dispute again. Ask the operator.",
        False,
    ),
    ErrorCode.DISPUTE_INVALID: CatalogueEntry(
        "The dispute was not accepted: check the criterion id, the code and the note.",
        "Use a public criterion id, one of the listed codes and a short plain-text note.",
        False,
    ),
    ErrorCode.DISPUTE_NOT_ALLOWED: CatalogueEntry(
        "A session that already holds a passing result cannot dispute.",
        "Promote the passing result, or keep working if you changed the code since.",
        False,
    ),
    ErrorCode.SESSION_DISPUTED: CatalogueEntry(
        "This session was closed by a dispute.",
        "Stop. The operator decides whether a fresh session may start.",
        False,
    ),
}


def _optional_count(value: int | None, what: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**32:
        raise ValueError(f"invalid {what}: {value!r}")


@dataclass(frozen=True, slots=True)
class BudgetState:
    """``None`` means unknown; it must never read as zero (P14)."""

    remaining: int | None = None
    limit: int | None = None

    def __post_init__(self) -> None:
        _optional_count(self.remaining, "remaining runs")
        _optional_count(self.limit, "run limit")
        if self.remaining is not None and self.limit is not None and self.remaining > self.limit:
            raise ValueError("remaining runs exceed the limit")


def _agent_path(value: str) -> None:
    if not value:
        return
    if (
        len(value) > PATH_LIMIT
        or not value.isprintable()
        or value.startswith("/")
        or "\\" in value
        or posixpath.normpath(value) != value
        or value.split("/")[0] == ".."
    ):
        raise ValueError(f"invalid agent-written path: {value!r}")


@dataclass(frozen=True, slots=True)
class ErrorDetails:
    """Whitelisted detail fields only (P7). Empty string means unset."""

    criterion_id: str = ""
    agent_written_path: str = ""
    budget: BudgetState | None = None
    host: str = ""

    def __post_init__(self) -> None:
        if self.criterion_id:
            ids.criterion_id(self.criterion_id)
        _agent_path(self.agent_written_path)
        if self.budget is not None and not isinstance(self.budget, BudgetState):
            raise ValueError("budget must be a BudgetState")
        if self.host:
            ids.host(self.host)


@dataclass(frozen=True, slots=True)
class GateError:
    code: ErrorCode
    reason: str
    hint: str
    retryable: bool
    details: ErrorDetails = field(default_factory=ErrorDetails)
    docs: str = ""
    pre_hidden_stderr_tail: str = ""

    def __post_init__(self) -> None:
        """Only catalogue text may reach the agent, even on direct construction."""
        if not isinstance(self.code, ErrorCode):
            raise ValueError(f"not an ErrorCode: {self.code!r}")
        entry = CATALOGUE[self.code]
        if self.hint != entry.hint or self.retryable is not entry.retryable:
            raise ValueError("hint and retryable must come from the catalogue")
        if self.docs != docs_anchor(self.code):
            raise ValueError("docs must be the catalogue anchor")
        if not isinstance(self.details, ErrorDetails):
            raise ValueError("details must be ErrorDetails")
        _check_reason(self.code, self.reason, entry.reason)
        if self.pre_hidden_stderr_tail:
            if self.code not in PRE_HIDDEN_STAGE_CODES:
                raise ValueError(f"{self.code.name} may not carry a stderr tail")
            if len(self.pre_hidden_stderr_tail.encode()) > STDERR_TAIL_LIMIT:
                raise ValueError("stderr tail exceeds the limit")


_CRITERION_MARK = " Criterion: "


def _check_reason(code: ErrorCode, reason: str, template: str) -> None:
    if reason == template:
        return
    if code is ErrorCode.CRITERION_FAILED and reason.startswith(template + _CRITERION_MARK):
        description = reason[len(template) + len(_CRITERION_MARK) :]
        if 0 < len(description) <= DESCRIPTION_LIMIT and description.strip():
            return
    raise ValueError("reason must be the catalogue template")


def docs_anchor(code: ErrorCode) -> str:
    return f"docs/errors.md#{code.value}"


def tail(text: str, limit: int = STDERR_TAIL_LIMIT) -> str:
    """Last ``limit`` bytes of ``text`` as UTF-8, never splitting a character."""
    raw = text.encode()
    if len(raw) <= limit:
        return text
    return raw[-limit:].decode("utf-8", errors="ignore")


def gate_error(
    code: ErrorCode,
    *,
    details: ErrorDetails | None = None,
    stderr_tail: str = "",
    public_description: str = "",
) -> GateError:
    """Build an error from the catalogue.

    ``stderr_tail`` is refused outside pre-hidden stages. ``public_description`` is
    the frozen, public criterion text and is only used with ``CRITERION_FAILED``.
    """
    if not isinstance(code, ErrorCode):
        raise ValueError(f"not an ErrorCode: {code!r}")
    if stderr_tail and code not in PRE_HIDDEN_STAGE_CODES:
        raise ValueError(f"{code.name} may not carry a stderr tail")
    if public_description and code is not ErrorCode.CRITERION_FAILED:
        raise ValueError("only CRITERION_FAILED carries a criterion description")
    entry = CATALOGUE[code]
    reason = entry.reason
    if public_description:
        reason = f"{reason}{_CRITERION_MARK}{public_description}"
    return GateError(
        code=code,
        reason=reason,
        hint=entry.hint,
        retryable=entry.retryable,
        details=details if details is not None else ErrorDetails(),
        docs=docs_anchor(code),
        pre_hidden_stderr_tail=tail(stderr_tail),
    )
