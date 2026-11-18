---
status: accepted
date: 2026-11-18
reviewers: [gpt-6.1-sol]
---
# 0017. Require a typed challenge on the controlling terminal for attended operator decisions

## Context
`freeze --approve --as`, `dispute resolve --as`, `author recover` (finish), `promote --approve`
on sensitive paths and `freeze --allow-auto` record an operator's decision. Any process running
the CLI as the operator's UID can pass them; ADR 0009 lets "a human or an orchestrator agent"
act as operator. A sandboxed agent cannot reach the CLI (ADR 0003), but a host agent can and
would decide on hidden tests under a person's name. This ADR narrows that route; it does not close it.

## Options
- **A**: Accept it. Document that `--as` trusts the host UID, and add no code.
- **B**: Typed challenge. The operator retypes a random code shown on `/dev/tty`.
- **C**: OS-level separation. A helper running as another UID (polkit or sudo) decides.

## Decision
**B** (operator's choice); **C** stays open. It revises ADR 0009's operator for these five decisions
only; withdrawing consent, an unpublished rollback and ADR 0016's auto route take no challenge.
- A `PresenceProof` port is called by the app use case under approval ownership, after loading
  state and before committing; state is revalidated after the answer. It returns a single-use
  confirmation, not a reusable boolean. No caller can skip it by choosing another route.
- The prompt names the operation, lineage, proposal digest or dispute id, actor, outcome and any
  waiver, with terminal controls escaped and no hidden-test content.
- The adapter opens `/dev/tty`, never stdin, and fails closed on no terminal, any open, flush,
  read or write error, EOF, hangup, cancel, overlong input or expiry. It flushes (`TCIFLUSH`)
  before showing a `secrets` code, reads with no echo under one monotonic deadline, restores the
  terminal in `finally`, allows one attempt and compares bounded bytes with `compare_digest`.
- No env var, flag or config skips it. Tests construct a fake port in Python; security tests also
  drive the real CLI with piped stdin, no terminal, typed-ahead input and timeout, and use a pty
  for success. The audit records `presence: tty-challenge`, a declared signal, not proof.

## Consequences
- ✅ Stdin-fed, preloaded and terminal-less invocations, without terminal emulation, can no longer decide.
- ⚠️ It proves terminal interaction, not a human: an agent driving its own pty (`expect`) passes;
  same-UID code or store tampering stays a trusted-host residual. Headless operators use ADR 0016.
