---
status: accepted
date: 2026-11-24
reviewers: [gpt-6.2-sol, gpt-6.2-astra, gemini-flash]
---
# 0015. Gate auto-freeze on a stored, bound strength-review record

## Context
ADR 0013 point 3 lets the host approve a freeze alone (`cheeseless-dev`) after "a passing strength
review by a `--strength-reviewer` of another vendor". That was never implemented: `approve_freeze`
checks every ADR 0005 precondition except this one, and has nothing to consult. A CLI flag cannot be
the evidence, because a flag is the approval; the dev loop refuses to auto-freeze (D13-6a).

## Options
- **A stored review record** the approval checks, written by the host after a review it launched.
- **Trust the CLI flag** (`--strength-reviewer` present means reviewed), or **never auto-freeze**.
## Decision
(A), because it is the only option where the evidence and the approval are different facts. The review
is an *additional* gate, never a substitute: every executable ADR 0005 check still runs, so this does
not reintroduce the LLM judge ADR 0009 rejected.
`StrengthReview` binds `proposal_id`, `acceptance_tree`, `spec_digest`, `baseline_commit` and the
reviewer's vendor, carries `green`, and only the host writes it to the trusted store.
1. **The host decides, not the caller.** A separate `auto_approve_freeze` use case sets
   `cheeseless-dev` itself; the `--as` label never selects the mode. It requires a current green
   crosscheck, refuses `--no-reference`, and `--confirm-freeze` routes to the operator.
2. **The verdict names what was reviewed: the acceptance tree, the spec digest and the baseline.**
   The host launches the reviewer on an immutable snapshot, records those three before the session
   and re-checks them at approval, so a live checkout cannot be swapped under it. A changed
   reference invalidates the *crosscheck*, which is caught there.
3. **`green` needs an explicit pass**, not exit 0; a crash, timeout or missing verdict forces
   operator approval. The verdict is a structured line the host parses, not a phrase it greps.
4. **Independence from launch config**: the host compares the vendor recorded at the *author's*
   launch with the one at the *reviewer's*, both from trusted config; an unknown or shared vendor
   refuses auto-approval. Not the current defaults, and never a reported model id.
The record is written only after the review ends and the seed drawn only after the approval, so neither
author nor reviewer sees the seed before the suite is fixed (ADR 0005, as ADR 0013 extends it).

## Consequences
- ✅ Auto-freeze is possible with evidence checkable afterwards, and impossible without it; a stale review is refused on the same three keys the proposal binds.
- ⚠️ The record proves a review *ran*, not that it was good: the author's test text is untrusted input the reviewer reads, so a pass can be argued for. User approval stays stronger.
- ⚠️ Vendor identity is only as trustworthy as the config declaring it.