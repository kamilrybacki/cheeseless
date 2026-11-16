# Error codes

Generated from `proto/cheeseless/v1/error.proto` by `scripts/gen_errors_doc.py`; do not
edit. Every agent-facing error carries one of these codes and a `docs` anchor pointing here
(ADR 0008). Pre-hidden stages may attach a DLP-scanned stderr tail of about 4 KB and
cost no run budget (Q51, Q52).

### `RUN_BUDGET_EXHAUSTED`

The run budget for this task is used up.

- **What to do:** Stop calling gate_run. Ask the operator to grant more runs.
- **Retryable:** no
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_RUN_BUDGET_EXHAUSTED` = 1

### `CRITERION_FAILED`

An acceptance criterion failed.

- **What to do:** Re-read the public criterion description and the contract tests, then fix the code.
- **Retryable:** yes
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_CRITERION_FAILED` = 2

### `BUILD_FAILED`

The project did not build.

- **What to do:** Fix the build error shown in the stderr tail. This attempt cost no run budget.
- **Retryable:** yes
- **Stderr tail (pre-hidden stage):** yes
- **Wire:** `ERROR_CODE_BUILD_FAILED` = 3

### `PROMOTE_PATH_REJECTED`

The change touches a path that may not be promoted.

- **What to do:** Keep changes inside src/, tests/, docs/ or README files.
- **Retryable:** no
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_PROMOTE_PATH_REJECTED` = 4

### `SENSITIVE_FILE_NEEDS_APPROVAL`

The change touches a sensitive file.

- **What to do:** Ask the operator to approve this file, or leave it unchanged.
- **Retryable:** no
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_SENSITIVE_FILE_NEEDS_APPROVAL` = 5

### `EGRESS_DOMAIN_DENIED`

The request went to a host that is not on the allowlist.

- **What to do:** Use only allowed hosts. Ask the operator if you need another one.
- **Retryable:** no
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_EGRESS_DOMAIN_DENIED` = 6

### `EGRESS_SERVER_TOOL_DENIED`

The request asked the model provider to run a server-side tool.

- **What to do:** Server-side tools such as web fetch or code execution are disabled in gated sessions.
- **Retryable:** no
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_EGRESS_SERVER_TOOL_DENIED` = 7

### `DLP_BLOCKED`

Outbound content matched protected material and the session was stopped.

- **What to do:** The operator has been alerted. Do not retry.
- **Retryable:** no
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_DLP_BLOCKED` = 8

### `INFRA_ERROR`

The gate failed for a reason unrelated to the candidate.

- **What to do:** Retry later. If it keeps failing, tell the operator.
- **Retryable:** yes
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_INFRA_ERROR` = 9

### `RATE_LIMITED`

Too many attempts before the hidden stage in a short time.

- **What to do:** Wait before retrying and fix the reported problem locally first.
- **Retryable:** yes
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_RATE_LIMITED` = 10

### `IMPORT_FAILED`

The package could not be imported.

- **What to do:** Fix the import error shown in the stderr tail. This attempt cost no run budget.
- **Retryable:** yes
- **Stderr tail (pre-hidden stage):** yes
- **Wire:** `ERROR_CODE_IMPORT_FAILED` = 11

### `EXECUTABLE_MISSING`

The command-line executable named by the contract was not found.

- **What to do:** Make sure the project installs the executable described in acceptance/contract/.
- **Retryable:** yes
- **Stderr tail (pre-hidden stage):** yes
- **Wire:** `ERROR_CODE_EXECUTABLE_MISSING` = 12

### `STARTUP_CRASH`

The program crashed on startup.

- **What to do:** Fix the crash shown in the stderr tail. This attempt cost no run budget.
- **Retryable:** yes
- **Stderr tail (pre-hidden stage):** yes
- **Wire:** `ERROR_CODE_STARTUP_CRASH` = 13

### `SMOKE_TEST_FAILED`

A public smoke test from acceptance/contract/ failed.

- **What to do:** Run the contract tests locally and fix the failure. This attempt cost no run budget.
- **Retryable:** yes
- **Stderr tail (pre-hidden stage):** yes
- **Wire:** `ERROR_CODE_SMOKE_TEST_FAILED` = 14

### `DISPUTE_DISABLED`

Disputes are not enabled for this repository.

- **What to do:** Keep working on the public criteria, or ask the operator to enable disputes.
- **Retryable:** no
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_DISPUTE_DISABLED` = 15

### `DISPUTE_CAP_REACHED`

The dispute limit for this task lineage is used up.

- **What to do:** Do not dispute again. Ask the operator.
- **Retryable:** no
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_DISPUTE_CAP_REACHED` = 16

### `DISPUTE_INVALID`

The dispute was not accepted: check the criterion id, the code and the note.

- **What to do:** Use a public criterion id, one of the listed codes and a short plain-text note.
- **Retryable:** no
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_DISPUTE_INVALID` = 17

### `DISPUTE_NOT_ALLOWED`

A session that already holds a passing result cannot dispute.

- **What to do:** Promote the passing result, or keep working if you changed the code since.
- **Retryable:** no
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_DISPUTE_NOT_ALLOWED` = 18

### `SESSION_DISPUTED`

This session was closed by a dispute.

- **What to do:** Stop. The operator decides whether a fresh session may start.
- **Retryable:** no
- **Stderr tail (pre-hidden stage):** no
- **Wire:** `ERROR_CODE_SESSION_DISPUTED` = 19
