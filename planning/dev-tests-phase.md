# Dev tests phase: reference → crosscheck → freeze (ADR 0013 stage 5, slice 2)

Working note for the next slice of the `cheeseless dev` loop. It decides nothing new:
ADR 0013 already fixes the rules. This records how the existing use cases are sequenced,
and the one place where the ADR's "strength reviewer" gate needs to be checked against
what the code can actually enforce today.

## What already exists

| Piece | Where | State |
|---|---|---|
| Reference attempt run + snapshot | `bootstrap_reference.py` | done (slice 3c) |
| `record_reference_snapshot` | `app/authoring.py` | done |
| `author_crosscheck` | `app/authoring.py` | done |
| `freeze_propose` / `approve_freeze(actor=…)` | `app/authoring.py` | done |
| Phase handover, preconditions | `app/dev_flow.py` | done (slice 1) |

So the tests phase is **composition**, exactly like the spec phase: the loop may not
re-implement a check `approve_freeze` already makes.

## Sequence the driver must enforce

1. **Reference must be `done`.** The tests phase cannot close until the reference attempt
   for the *current spec digest* is `done` and its snapshot is bound. A `failed` or
   `cancelled` attempt is not a pass; the loop reports it and stops.
2. **Crosscheck must be green and bound.** `author_crosscheck` already refuses without a
   bound reference, and `_crosscheck_problem` re-checks binding against the proposal. The
   driver calls the existing functions; it does not add a second binding rule.
3. **Freeze approval.** `approve_freeze` requires `PROPOSED`, merged, crosschecked, RED
   baseline — all inside `_ready`. The driver chooses the *actor*:
   - `--strength-reviewer -- <agent>` present **and** the review passed → the host may
     approve as `cheeseless-dev` (ADR 0013 point 3);
   - otherwise (or with `--confirm-freeze`) → the user approves, as today.

## The open question this slice must not paper over

ADR 0013 requires, for the host to approve alone, "a passing strength review by a
`--strength-reviewer` of another vendor (read-only sandbox)". Two things are **not yet
built**:

- the strength-reviewer sandbox itself (read-only over spec + hidden tests);
- a stored, queryable record of "this review passed", which `approve_freeze` could check.

Until both exist, `approve_freeze(actor="cheeseless-dev")` has **no evidence to consult**:
nothing in the store can distinguish "a reviewer passed this proposal" from "the caller
said so". Letting the driver pass `actor="cheeseless-dev"` on the strength of a CLI flag
would make the flag itself the approval — an agent-launched command could then freeze its
own tests, which ADR 0009 exists to prevent.

**Decision for this slice (D13-6a):** the driver never approves as `cheeseless-dev`. It
prepares and proposes, then requires the user's approval, exactly as the non-auto-freeze
path does. The auto-freeze branch stays closed until a review record exists in the store.
This is the conservative reading of ADR 0013 and it errs toward 0009's property.

## What this slice adds

- `app/dev_flow.py`: `reference_ready(...)` — a pure predicate over the stored reference
  record and crosscheck that says whether the phase may close, and *why not* when it may
  not. It reads; it never writes.
- `bootstrap_dev.py`: the tests-phase branch that runs it and prints the outcome.
- No new store port, no new sandbox, no change to `approve_freeze`.

## Tests

- unit: reference `failed`/`cancelled`/missing refuses with a distinguishable reason;
  a green bound crosscheck of the same spec digest passes; a crosscheck for another
  digest is stale.
- security: the driver cannot reach `cheeseless-dev` approval; `reference_ready` writes nothing.
