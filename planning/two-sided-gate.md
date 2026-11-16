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
4. Author promotes onto `spec/T`; CI author job passes; operator merges.
5. Operator: `cheeseless freeze --approve <id> --as <actor> --json` (checks merged tree, crosscheck GREEN, baseline RED; draws seed).
6. Operator: `cheeseless session --role implementer --task T` → `gate_run` until PASS → promote onto `gate/T`.

## Dispute path (only with `dispute.enabled: true`)

1. Implementer: `gate_dispute(criterion_id, code, note)` → session ends `DISPUTED`.
2. Operator: `cheeseless disputes --json` (id, criterion, code; note only with `--show-note`).
3. Operator: `cheeseless dispute resolve <id> --upheld|--rejected --as <actor>`.
4. Upheld → back to step 1 of the happy path in the same lineage; rejected → new implementer session.

Codes: `contradicts_spec`, `untestable`, `ambiguous_spec`, `other`. Disputes per lineage: 3.
