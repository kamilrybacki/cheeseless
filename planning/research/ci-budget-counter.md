# Trusted cross-run CI budget counter (closes ledger P10-6, P10-13, P10-19)

Design only; no code changed. Status: proposal, revision 4. Revision 1 (check runs as records) was rejected
by review a2a98b11 (gpt-6.1-sol); its nine findings and how this revision answers them are in section 9 and in
ledger P10-23. Revision 2 was accepted with fixes by review 0200f03e (gpt-6.1-sol); its five findings are in
section 10 and ledger P10-25. Revision 3 was accepted with fixes by review 6ba6921f (gpt-6.1-terra); its five
findings are in section 11 and ledger P10-26. Docs were checked 2026-11-14 against docs.github.com. "UNVERIFIED" marks every claim
the docs did not settle. Each one is listed in section 6 and must pass on real GitHub (P10-7) before the counter
ships; if any fails, the external store fallback (section 2e) is used. A judge with no store configured fails
closed.

## 1. Threat model
Attacker = PR author or coding agent with write (push) access. **Can:** push branches, run its own workflows on
them (own `GITHUB_TOKEN`), open PRs, re-run `ci` so `workflow_run` re-fires the judge, delete workflow runs,
create check runs as the shared github-actions app. **Cannot:** hold judge secrets or the judge App key, change
the default-branch workflow or `acceptance/`, or act as admin (rulesets, Apps, environments).
Goal: more than `limit` (5) judge runs reach hidden input per lineage. Inflating the count only exhausts the budget
(a denial the attacker already has by re-running), so only **lowering the observed count** (delete, roll back,
hide, reset, replay) is a bypass.
Fixed separately in `docs/ci` (ledger P10-22): secrets live in the `cheeseless-judge` environment limited to the
default branch (review #2), and the verdict is posted by the judge App, not `GITHUB_TOKEN` (review #7).

## 2. Candidate stores
| | store | why | verdict |
|-|-|-|-|
| a | unprotected ref or branch | pusher force-updates or deletes it, restoring an older valid state | reject |
| b | Actions variables | no CAS, no history, long-lived PAT in the judge | reject |
| c | check runs by a dedicated App | not durable (retention), not linearizable, payload copyable (review #1, #3, #4) | reject |
| d | **protected append-only ledger branch** | GitHub refuses rewind and delete for everyone but admins; CAS by fast-forward | **recommend** (UNVERIFIED) |
| e | external store with atomic conditional write | adopter-provided behind the port | fallback |

**(c) rejected.** Third-party check runs fall under the retention policy GitHub applies from 2026-10-01
(default 90 days, at most 90 on public repos), so a vanished record silently refunds a run. There is no
linearizable listing or id ordering, so "rank after create" is not a safe race check. And a valid summary can be
copied onto another record unless the MAC binds record identity.

## 3. Recommendation: append-only ledger branch per lineage
**Branch.** `cheeseless/budget/<lineage>`, one per lineage, CI pool only (local keeps `FileStore`).

**Ledger repository (review 0200f03e #2).** The branches live in a dedicated private ledger repository, not in
the judged one:
- the judge App is installed on both repositories;
- pushers of the judged repository get no access to the ledger repository;
- the budget token is minted with `repositories: <ledger repo>` and `permission-contents: write`. A leaked budget
  token therefore cannot touch branches or tags of the judged repository.

The same-repository layout is still allowed, but only as an opt-out, with the residual risk listed below. The
bundle pins the ledger repository.

**Two rulesets on `cheeseless/budget/**`** in the ledger repository (operator setup, documented in
`docs/ci/README.md`). They are defence in depth there, because pushers have no access to that repository at all:
- *Ruleset A, no bypass actors:* restrict deletions and block force pushes. Only an admin who edits the ruleset can
  rewind or delete, and the rule **binds the judge App too**.
- *Ruleset B, bypass = judge App only (mode "always"):* restrict creations and restrict updates, so pushers cannot
  create the branch or append to it.

Together they leave the App able only to fast-forward, and nobody else able to write. Two points are UNVERIFIED:
that bypass is per ruleset, so A still binds the App; and that the target account has rulesets at all (private
repos need GitHub Pro, Team or Enterprise; operator item). The docs do list Apps as valid bypass actors.

**Seed.** The operator runs `cheeseless ci-budget-init` once per lineage, with admin credentials. It creates the
branch with one root commit whose message is the seed record:
- record: `{v:1, kind:"seed", repo_id, lineage, pool:"ci", mac}`;
- `mac = HMAC(k_b, "budget-seed-v1|" + repo_id + "|" + lineage + "|ci")`;
- `k_b = HMAC(store_key, "cheeseless-budget-v1")`, a key derived from the store-wide HMAC key the bundle already
  carries. That key belongs to the store, not the task, so it survives re-freeze and holdout rotation (D14).

Bundle v2 pins `budget: {repo, branch, root}`, where `repo` is the ledger repository and `root` is the root commit
SHA. The seed is never counted. `repo_id` in every MAC is the id of the *judged* repository.

**Record.** Each charge is one commit whose only parent is the previous tip. Its tree holds one file,
`record.json`, and the commit message is the same JSON:
- record: `{v:1, kind:"run", repo_id, lineage, pool:"ci", seq, parent, run_key, claim, mac}`;
- `seq` starts at 1, `parent` is the parent commit SHA, `run_key = <run_id>/<run_attempt>` of the judge
  workflow run;
- `claim` is 128 random bits that the judge process draws once at start and keeps in memory (review 6ba6921f #2);
- `mac = HMAC(k_b, "budget-run-v1|" + repo_id + "|" + lineage + "|ci|" + seq + "|" + parent + "|" + run_key +
  "|" + claim)`.

**Wire format (review 6ba6921f #4).** `record.json` in the tree is authoritative. The encoding is canonical JSON:
UTF-8, sorted keys, no insignificant whitespace, integers in decimal. The schema is strict:
- `v` must be 1 and `kind` must be `seed` (root only) or `run` (non-root only);
- every field is required and no other field is allowed;
- the tree holds exactly `record.json` and nothing else;
- the commit message must equal the blob's bytes exactly.

Anything else is a validation failure, never a best-effort parse.

Because `parent` and `seq` are bound, a copied payload is invalid at any other position (review #4).

**Expected identity (review 6ba6921f #5).** The validator never takes identity from the ledger. The expected
`lineage` comes from the HMAC-verified task record in the bundle. The expected `repo_id` comes from the
`workflow_run` event of the default-branch judge. The bundle's `{repo, branch, root}` must match both. A valid
tuple swapped in from another lineage or judged repository fails on the branch name, the seed MAC or the record
identity. Re-freeze keeps the lineage, so it keeps the same tuple.

**Validation** (`domain/budget_ledger.py`, pure). Walk first parents from the tip back to the parentless commit.
Any of the following yields BLOCKED/`INFRA_ERROR`:
- the branch is missing, or the root is not the pinned root;
- the pinned branch is not exactly `cheeseless/budget/<lineage>` for the authenticated lineage (review 6ba6921f #5);
- a commit breaks the wire format above;
- the seed MAC is bad, or the seed has any parent (it must have exactly zero; review 0200f03e #4);
- a run record has more or fewer than exactly one parent;
- a record's MAC is bad;
- a record names another repo, lineage or pool;
- `seq` is not contiguous;
- `parent` is not the commit's actual parent;
- a `run_key` appears twice;
- the chain is longer than `limit + 1` commits.

None of these cases ever re-initialises the ledger (review #6). For a valid chain, the count is the number of run
records. A seed-only chain is valid and counts 0.

**`reserve_run(lineage, CI)`** (adapter loop, at most 3 CAS attempts):
1. Read the tip and validate the chain. This is bounded: at most `limit + 2` commit reads.
2. If a record with our `run_key` exists:
   - **with our `claim`:** an earlier try in this process already committed and only the response was lost.
     Return `Budget(limit, seq)` if `seq <= limit`, else refuse. A reused record still goes through admission
     (review #5).
   - **with another `claim`:** another judge process holds this attempt. Refuse with BLOCKED/`INFRA_ERROR` and an
     operator alert, and never reach hidden input. Validation already rejects a second record with the same
     `run_key`, so a second process cannot append one. Under P1 this makes the record a one-shot execution claim:
     at most one process per attempt reaches hidden execution (review 6ba6921f #2).
3. If count >= limit, return `None` and **write nothing** (review #5).
4. Otherwise create the record's blob, tree and commit (parent = tip), then update the branch **without force**.
   A non-fast-forward update is rejected, which makes this the compare-and-swap. It is only correct if the server
   does the ancestry check and the update atomically; that is a **backend prerequisite** (P1 below), not
   something this loop can repair.
5. On success, re-read the branch and require our commit on the first-parent chain (a sanity check, not a
   correctness mechanism). On a non-fast-forward rejection, or an ambiguous update (timeout, 5xx), go back to
   step 1:
   - if the update had in fact been applied, step 2 finds it;
   - a stale commit can never land later, because its parent is no longer the tip.
6. If the CAS attempts run out, or a transport error is still there after 3 retries, raise `CounterError`. The
   result is BLOCKED/`INFRA_ERROR`, no PASS is posted and the merge stays blocked (I6).

**Linearizability (review #1, corrected by review 0200f03e #1).**

*Prerequisite P1.* The backend must do a non-force ref update as one atomic step: check ancestry, then update.
With P1, the ref is a single value that only moves forward, so concurrent reserves are ordered by which
fast-forward wins, and two records with the same parent cannot both land.

*No compensation without P1.* Revision 2 claimed step 5's post-check catches a non-atomic server. That is false.
Take a count of 4:
1. A and B both build a charge on the same tip.
2. A's update lands, A reads its commit back and starts hidden execution.
3. B's update overwrites A's, B reads its own commit back and starts hidden execution.

Both checks pass, six runs reach hidden input, and the ledger shows five.

*Status of P1.* GitHub documents that a non-force update is rejected unless it is a fast-forward. It does not
document the concurrency guarantee. A live race test (section 6) is evidence, not proof.
- The operator must accept P1 explicitly, as a ledger row naming the evidence.
- Otherwise the counter uses option (e), whose store must document an atomic conditional write.

*After-the-fact detection (best effort).* The judge re-reads the chain once more after hidden execution. If its
own commit is no longer on the chain, it posts no PASS and raises an operator alert with the lineage and the two
commit SHAs. This catches a P1 violation only when the overwrite lands before that read (review 6ba6921f #1):
- if B overwrites A after A's final read, A posts PASS and nothing records the lost charge;
- later reads see only the winning chain, so the loss is then invisible.

Detection is therefore not a guarantee and never a substitute for P1. P1 acceptance, or an option (e) store that
documents an atomic conditional write, stays mandatory before release.

**Durability (review #3).** Git objects reachable from a protected branch are not subject to check-run or
workflow-run retention. Removing them means an admin edits ruleset A, which is out of model.

**Residual risk (review 0200f03e #2).**
- Admins and holders of the App key can alter or reset the ledger.
- A leaked budget token cannot rewind, because ruleset A has no bypass.
- A leaked budget token can append arbitrary commits. Unsigned ones make validation fail, so every later judge
  run on that lineage is BLOCKED until an admin repairs the ledger. That is a ledger denial of service, not only
  an inflated count.
- Dedicated ledger repository (default): the damage stays inside the ledger repository.
- Same-repository opt-out: a Contents-write token can also create, update or delete every unprotected branch and
  tag of the judged repository. Its authority is the App installation's, whatever the candidate-isolation tests
  show. Those tests prove the token never reaches the candidate; they do not narrow what the token can do.

The judge logs the observed count on every run.

## 4. Charging and credentials
Reserve at the existing point: after smoke, before the hidden `runner.run` (`app/ci_judge.py`, step (d)).
- Pre-hidden failures create no record (Q52): protected path, drift, build, smoke, and a counter failure *before*
  any update was sent.
- An ambiguous reservation can be durable without being observed (review 0200f03e #5). Suppose GitHub commits the
  record, the response is lost, and the API stays unavailable through every retry. The judge then raises
  `CounterError` and blocks before hidden execution, but the record stays. That run is charged without hidden
  input. The charge is kept, never refunded: a refund path would be a rollback primitive.
- After reserve, every failure keeps the record (D2), including INFRA_ERROR and a job cancelled after reserve.
- Hidden execution happens at most once per judge process. At most once per workflow run attempt follows from
  the one-shot `claim` (section 3, step 2), not from the workflow shape. The template also keeps one judge job
  with no matrix and no step retry, as defence in depth.
- A re-run is a new `run_attempt` and therefore a new charge.

**Credential.** The reservation happens inside the judge process and needs an App token with Contents write.
- The workflow mints a second, separate token right before the judge step: same App, `permission-contents: write`
  only, `repositories:` the ledger repository only. It goes only to the judge step, as `CHEESELESS_BUDGET_TOKEN`.
- The checks-only verdict token is still minted after the judge.
- The runner process reads the budget token and removes it from its own environment before building any sandbox
  env. The candidate bwrap env comes from an allowlist that never includes it (canary test).
- Both tokens are revoked at job end.

Contents write covers the whole of every repository the token names. That is why the token names only the ledger
repository. In the same-repository opt-out, default-branch protection must still bind the App, which means it is
not a bypass actor there (operator check, section 6). The existing test
`test_judge_step_never_sees_the_app_credentials` changes from "no App token" to "only the contents-write budget
token, never the key and never the checks token".

## 5. Port and adapter shape (review #8)
- **Port.** `ports/run_counter.py`: `RunCounter` with exactly `reserve_run(lineage_id, pool) -> Budget | None` and
  `budget(lineage_id, pool) -> Budget`, plus `CounterError(StoreError)`. `FileStore` already satisfies it
  structurally. `judge(...)` gains `counter: RunCounter`. The domain never imports ports.
- **Codec.** Record encoding, MAC and chain validation are pure functions in `domain/budget_ledger.py`. Bundle v2
  decoding stays in `ci_bundle.py`. The CI adapter holds neither.
- **Adapter.** `adapters/outbound/ci/github_ledger.py` is transport only: get ref, get commit, create
  blob/tree/commit, non-force ref update, all against the ledger repository named in the bundle, never the judged
  one. It sits behind an internal `LedgerTransport` Protocol with a
  stdlib-urllib implementation.
- **Wiring.** `bootstrap_ci` is the only place that wires it: limit from `bundle.ci_limit` (retires P10-13 and
  P10-19), ledger repo, branch and root from the bundle, judged repo id and run key from the event, budget token
  from env. A bundle without a ledger repo fails closed.
- **Fakes.** `tests/ci_support.py` gets `FakeLedgerTransport`: an in-memory ref with a strict fast-forward check
  and scriptable faults (lost response, ambiguous write, non-fast-forward race, 5xx).

## 6. Release gate on real GitHub (P10-7, operator)
**Operator prerequisites:** the judge App is installed on the judged and ledger repositories; the
`cheeseless-judge` environment exists with a deployment-branch rule for the default branch only; the required
check is pinned to the judge App; rulesets A and B exist in the ledger repository. Live tests use disposable
canary secrets, never the real bundle.

*Counter store:*
1. Rulesets are available on the ledger repository's plan.
2. Bypass is per ruleset: A binds the App while B lets it through.
3. Ruleset matrix (review 6ba6921f #3). For each operation (deletion, force update, rewind through the REST refs
   API, creation of a new `cheeseless/budget/*` branch, fast-forward append), and for each actor (a judged-repo
   pusher token, a budget token and the judge App itself):
   - every combination is rejected, except a fast-forward append by the judge App;
   - the App's fast-forward is accepted (positive);
   - the App's deletion and force update are rejected, because ruleset A has no bypass.

   Only the operator's `ci-budget-init` may create a branch.
4. P1 evidence: a non-force ref update under 10 concurrent writers lets exactly one win per tip. This is evidence
   for the operator's P1 acceptance, not proof (section 3).
5. A read made after a successful update reflects it.

*Tokens:*

6. The budget token (`permission-contents: write`, `repositories:` ledger) cannot write Contents in the judged
   repository and cannot create a check run.
7. The verdict token (`permission-checks: write`) cannot write Contents in either repository.
8. Same-repository opt-out only: default-branch protection still binds the App.

*Workflow isolation (review 0200f03e #3):*

9. A workflow that a pusher adds on a non-default branch cannot read the canary environment secret (negative).
10. The genuine `workflow_run` judge on the default branch can read it (positive).
11. A check run with the required check's name, posted by `github-actions` (a branch workflow with
    `checks: write`), does not satisfy the required check. One posted by the judge App does.

*Seeding (P10-29):*
12. With Ruleset B active, `POST git/refs` for a new `cheeseless/budget/**` branch is rejected (negative).
    Record the HTTP status and body; `ci-budget-init` treats 401, 403 and 422 as a refusal.
13. `GET git/commits/{sha}` returns a commit that no ref points at yet, right after it is written, and
    `ci-budget-init` on a fresh ledger repository initialized with one commit succeeds (positive).

The YAML unit tests in `tests/unit/test_judge_workflow.py` only pin the template. They cannot establish
environment rules, ruleset enforcement or required-check source pinning; checks 3 and 9–11 do. If any check fails, use option (e) or keep CI
unbudgeted and blocked.

## 7. Test plan (TDD, red first)
`tests/security/test_ci_budget_counter.py`, reusing the `Boom` runner and the canary from
`test_ci_judge_canary.py`:
1. **Sixth run refused.** Five runs, each with a fresh ephemeral `FileStore` and the same fake ledger, reach hidden
   execution. The sixth returns BLOCKED/`BUDGET_EXHAUSTED`. Assert that **hidden execution was never reached**
   (`runner.run` not called, canary absent), not that the sandbox was untouched, since smoke uses it. Also assert
   that nothing was written and no PASS was posted.
2. **Tampered ledger refused.** Parametrised over:
   - missing branch, wrong root, a seed with a parent;
   - bad seed MAC, bad record MAC;
   - a record copied onto another parent, wrong repo/lineage/pool;
   - non-contiguous seq, merge commit, duplicate run_key, over-long chain;
   - an unsigned commit appended by a leaked token;
   - wire format: unknown `v`, unknown `kind`, `seed` at a non-root, `run` at the root, an extra or missing field,
     non-canonical JSON, an extra file in the tree, a message that differs from the blob;
   - identity: a valid `{repo, branch, root}` from another lineage, one from another judged repository, and a
     branch name that does not match the lineage. Re-freeze of the same lineage keeps working.

   Each case gives BLOCKED/`INFRA_ERROR`, a non-zero exit and no PASS. A seed-only ledger is valid: the first
   reservation gets seq 1.
3. **Race.** At 4/5, two concurrent reserves on the atomic fake yield exactly one `Budget` and one `None`.
   A non-atomic fake, test both orderings. If the overwrite lands before the post-hidden read, expect an alert and
   no PASS. If it lands after that read, the run passes and the charge is lost. That test pins the documented
   limit, so nobody can later read detection as a guarantee.
4. **Ambiguous reservation.** The fake commits, drops the response, then fails every retry. Expect BLOCKED/
   `INFRA_ERROR`, hidden execution not reached, and the record still on the chain on the next read (not refunded).
5. **Duplicate attempt.** Two judge processes with the same `run_key` and different claims run concurrently on the
   atomic fake. Exactly one reaches hidden execution. The other is BLOCKED/`INFRA_ERROR`, never calls `runner.run`
   and raises an alert. A lost response within one process is still re-admitted.
6. **Token isolation.** The budget token never reaches the candidate build or run env (canary). The judge step
   never sees the App key.

`tests/unit/`:
- A `RunCounter` **charging** conformance suite, run against `FileStore`, `FakeCounter` and the ledger adapter:
  reserve to the limit, refuse after it, a refusal writes nothing, `budget()` view.
- A separate **transport idempotency** suite for the adapter only: a lost response is reused, a reused record above
  the limit is refused, an ambiguous write is resolved, exhausted CAS raises `CounterError`, a 5xx is retried 3
  times.
- Ordering tests: pre-hidden failures never call the counter; an INFRA_ERROR after reserve keeps the charge.
- Bundle v2 round trip and tamper.
- Workflow template tests for the two-token layout.

## 8. Proposed ADR 0006 text (status stays `proposed`; 35 -> 38 lines)
It states the required property only; the mechanism stays in this document (review #9).
```diff
@@ jobs get no secrets and a read-only token; no `pull_request_target` with a PR checkout.
+- The CI run budget (I4) is charged per lineage by a trusted, atomic and persistent counter outside
+  the runner, before hidden input; a missing, unreadable or inconsistent counter blocks the merge (I6).
+  Judge secrets live in an environment limited to the default branch; the verdict is posted by a judge App.
@@ - ⚠️ Fixing the allowlist per language is ongoing maintenance.
+- ⚠️ CI needs operator setup (App, environment, counter store); admins can still alter the count.
```
`reviewers` is filled in only after a real cross-model review. Line positions come from `git diff` when the change
is applied.

## 9. Review a2a98b11 findings
| # | sev | finding | answer |
|-|-|-|-|
| 1 | HIGH | rank check not linearizable | fast-forward CAS on one ref, post-write chain check (section 3) |
| 2 | CRIT | repo secret readable by branch workflows | fixed: environment limited to the default branch (P10-22) |
| 3 | HIGH | check runs not durable | check runs rejected; git objects on a protected branch |
| 4 | HIGH | payload copyable across records | MAC binds repo, lineage, pool, seq, parent, run_key; duplicates refused; separate seed MAC |
| 5 | MED | idempotency | run_key = run_id/run_attempt; a reused record is re-admitted; a refusal writes nothing; tests |
| 6 | MED | identity across re-freeze | store-key-derived `k_b`; bundle pins branch and root; a missing ledger means BLOCKED |
| 7 | HIGH | verdict forgeable via GITHUB_TOKEN | fixed: the judge App posts, the required check is pinned to it (P10-22) |
| 8 | LOW | layering and test precision | codec in domain, transport-only adapter, split suites, "hidden not reached" assertion |
| 9 | MED | ADR overstates | the ADR states the property only (section 8) |

## 10. Review 0200f03e findings (revision 2, accept with fixes)
| # | sev | finding | answer |
|-|-|-|-|
| 1 | HIGH | post-write check cannot repair a non-atomic update | atomic ancestry-check-and-update is prerequisite P1; compensation claim removed; operator accepts P1 or uses (e); post-hidden re-check only detects (section 3) |
| 2 | MED | leaked token can do more than inflate | dedicated ledger repo with a repo-scoped token; DoS and same-repo damage listed as residual (section 3) |
| 3 | MED | release gate skips environment and check-source controls | live checks 6, 7, 9–11 with canary secrets; operator prerequisites (section 6) |
| 4 | LOW | validation rejects the parentless seed | seed exactly zero parents, records exactly one; seed-only and first-reservation tests |
| 5 | LOW | outage can still charge | ambiguous reservation charged without hidden input, never refunded; test 4 |

## 11. Review 6ba6921f findings (revision 3, accept with fixes; gpt-6.1-terra)
| # | sev | finding | answer |
|-|-|-|-|
| 1 | HIGH | post-hidden re-check misses an overwrite that lands after it | stated as best effort with both orderings; P1 or an atomic (e) store stays mandatory (section 3); race test pins the limit |
| 2 | HIGH | two processes with one `run_key` both reach hidden input | per-process `claim` in the MAC'd record; another claim on our `run_key` is BLOCKED plus an alert; duplicate-attempt test 5 |
| 3 | MED | live checks do not cover each ruleset operation and actor | check 3 is now an operation × actor matrix with a positive App append |
| 4 | MED | record encoding ambiguous between tree and message | `record.json` is authoritative, canonical JSON, strict schema, single-file tree, message equals blob; malformed cases in test 2 |
| 5 | MED | bundle tuple not bound to lineage and judged repo | expected identity from the authenticated task and the event; branch name check; cross-lineage and cross-repo substitution tests |
