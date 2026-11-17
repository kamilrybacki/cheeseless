# Two-sided gate: lifecycle (working material for ADR 0009)

Every step is a host CLI call with `--json` (human or orchestrator agent) or a tool call
inside one role's session. Approve, resolve and grant run only on the host.

## States of a task

| State | Entered by | Leaves to |
|-------|-----------|-----------|
| `AUTHORING` | `cheeseless session --role author` | `PROPOSED`, `REFERENCE_BLOCKED` |
| `REFERENCE_BLOCKED` | reference session fails to build or exceeds retry cap (default 2) | `AUTHORING` (spec edit) or `PROPOSED` with `--no-reference --reason` at approve |
| `PROPOSED` | `freeze_propose` (author) | `AUTHORING` (stale: tree changed), `FROZEN` |
| `FROZEN` | `cheeseless freeze --approve <id>` | `IMPLEMENTING` |
| `IMPLEMENTING` | `cheeseless session --role implementer` | `PASSED`, `BLOCKED` (budget), `DISPUTED` |
| `DISPUTED` | `gate_dispute` (implementer) | `AUTHORING` (upheld, re-freeze same lineage), `IMPLEMENTING` (rejected, fresh session) |

## Happy path

1. Operator: `cheeseless session --role author --task T` → author edits `acceptance/`.
2. Operator: `cheeseless session --role reference --task T` → host snapshots the tree into the store.
3. Author: `author_check` → `author_crosscheck` → `freeze_propose`.
4. Author promotes onto `spec/<lineage>` (the authoring lineage id of task T); the branch-policy check passes (path confinement; the author check is advisory); operator merges.
5. Operator: `cheeseless freeze --approve <id> --as <actor> --json` (checks merged tree, crosscheck GREEN, baseline RED; draws seed).
6. Operator: `cheeseless session --role implementer --task T` → `gate_run` until PASS → promote onto `gate/T`.

## Dispute path (only with `dispute.enabled: true`)

1. Implementer: `gate_dispute(criterion_id, code, note)` → session ends `DISPUTED`.
2. Operator: `cheeseless disputes --json` (id, criterion, code; note only with `--show-note`).
3. Operator: `cheeseless dispute resolve <id> --upheld|--rejected --as <actor>`.
4. Upheld → back to step 1 of the happy path in the same lineage; rejected → new implementer session.

Codes: `contradicts_spec`, `untestable`, `ambiguous_spec`, `other`. Disputes per lineage: 3.

## CI routing

One trusted workflow (`docs/ci/judge.yml`) resolves the live PR, verifies base, head and branch class, then runs
`route` -> `policy` (no PR code: class and path confinement, job output), `judge` (gate/ only) and `evaluate` (advisory,
unprivileged, never consumed) -> `post` (the only publisher, no PR code). Two Apps: the ledger App (Contents, ledger repo
only) is the judge's only secret; the checks App (Checks only) is minted in `post` only. The verdict carries the judged
base; `post` fails a gate whose base is no longer the default-branch tip.
Branch protection requires ONE check, `cheeseless gate` (required checks are scoped by base branch): for `gate/<task>`
it is success only if the judge PASSED and the branch policy passed; for `spec/<lineage>` it is success if the branch
policy passed and says it is NOT an implementation verdict; any other class or failure is a failure. It is never
neutral or skipped. `cheeseless judge` and `cheeseless branch policy` stay as informational checks. An author check is
never an implementation PASS. `spec/<lineage>` is ADR 0009's `spec/<task>` (ledger, stage (b) row Q3).

`promote --role` is the operator's publication mode on the host, not session authentication. Binding a session to a role at
launch stays deferred (see the ledger's "Remaining" line).
