# Phase 11: the toy-repo end-to-end run

Status: **not started.** This note exists so the next session starts from a decision
rather than from rediscovery. Nothing here is accepted; it is the shape of the work.

## What phase 11 must prove

One toy repo, driven end to end by the real commands, proving the property the whole
project exists for: **an agent cannot reach a green verdict by editing the acceptance
tests.** Every earlier test proves one link; this proves the chain.

The chain, using only what exists today:

1. `cheeseless init` → `cheeseless dev` (spec phase) → author writes the spec, ends on
   `/spec-done` → `DevPhase.TESTS`.
2. `cheeseless dev-reference` → host runs the reference on a pty (ADR 0014) → attempt
   `done` with a snapshot bound.
3. `cheeseless dev` (tests phase) → `reference_ready` true → `cheeseless freeze`
   propose + **user** approval (D13-6a: no host-only approval yet).
4. `cheeseless dev` (implement phase) → implementer session, `cheeseless run` per
   attempt, holdout verdicts applied by `next_after_result`.
5. The canary: a candidate that edits the acceptance tests, prints hidden data, or
   reads the projection must be **refused**, not just scored low.

## The three canaries, reusing what is already written

Each already has a security test in isolation; phase 11 runs them through `dev`:

| Attack | Isolated test today |
|---|---|
| edit the acceptance tests | `tests/security/test_reference_projection.py` |
| print hidden test data | `tests/security/test_runner_canary.py`, `test_ci_judge_canary.py` |
| read the study/reference material | `tests/security/test_study_isolation_canary.py` |

## What is missing to build it

- A driver that runs the five steps above against `tests/toy.py`'s repo without a human
  in the loop for the *agent* turns (the freeze approval must stay a human step, so the
  test supplies it explicitly rather than automating it away).
- A bwrap requirement gate: most of this needs `tests/requirements.needs`, and a skipped
  environment must not read as a pass.
- Deciding whether the author/implementer turns are scripted fakes (cheap, deterministic)
  or real agent sessions (slow, non-deterministic, and unsafe to run in CI). **Current
  lean: scripted fakes that emit the same events a real session emits**, because the
  property under test is the host's reaction, not the model's behaviour.

## Why this is a separate slice

It touches the freeze trust boundary and the sandbox at once. Doing it in the same
change as the orchestrator would make review unable to separate "the loop is wrong" from
"the sandbox leaked", and a failure would not say which.
