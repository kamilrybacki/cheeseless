---
status: proposed
date: 2026-11-18
reviewers: []
---
# 0017. Require a typed challenge on the controlling terminal for attended operator decisions

## Context
`freeze --approve <id> --as <name>` and `dispute resolve --as <name>` record a person's
decision, but anything that can run the CLI as the operator's UID can pass them. A sandboxed
agent cannot reach the CLI (ADR 0003). An agent running on the host outside cheeseless can,
and it would freeze or uphold against hidden tests under a human's name. ADR 0001 accepts the
same UID as a residual. This ADR narrows that residual and leaves it in place.

## Options
- **A**: Accept it. Document that `--as` trusts the host UID, and add no code.
- **B**: Typed challenge. The approver retypes a random code shown on `/dev/tty`.
- **C**: OS-level separation. A helper running as another UID (polkit or sudo) approves.

## Decision
**B** (operator's choice): it stops scripted and piped approvals cheaply; **C** stays open.
- The check is a `PresenceProof` port. `approve_freeze` (attended branch) and `resolve_dispute`
  require it in the app layer, so no caller skips it by choosing a different route.
  `auto_approve_freeze` stays ADR 0016's route and takes no proof.
- The adapter opens `/dev/tty` itself and never reads stdin. It fails closed with no terminal,
  flushes typed-ahead input (`TCIFLUSH`) and shows the proposal or dispute id. It then wants a
  `secrets` code within a timeout, gives one attempt and compares in constant time.
- No env var, flag or config skips it; tests inject a fake port in-process. The audit
  record says `presence: tty-challenge`, a declared signal, not proof.
- Out of scope: `author recover --as`, which only clears a dead approver's reservation and
  freezes nothing. Also `promote --approve`, because a sensitive-path promote still lands
  through a GitHub PR and CI (ADR 0006).

## Consequences
- ✅ An agent that pipes text into the CLI or runs it with no terminal can no longer approve.
- ⚠️ Not a boundary: a same-UID agent driving a pty, or TIOCSTI where the kernel allows it,
  passes. It extends ADR 0009's "the host is a human" and supersedes nothing.
- ⚠️ Attended approval needs an interactive terminal, so headless operators use ADR 0016.
