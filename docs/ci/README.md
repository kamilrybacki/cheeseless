# CI judge setup

The CI judge runs the FULL suite (visible and holdout) against a pull request head and
posts a check run with the aggregate status and visible-criterion statuses only.
`docs/ci/judge.yml` is a template; do not install it in this repository.

## Setup

1. Create a dedicated **private ledger repository** under the same owner as the judged
   repository (for example `cheeseless-ledger`). It holds the cross-run CI budget counter,
   one branch `cheeseless/budget/<lineage>` per lineage. Pushers of the judged repository get
   no access to it. Initialize it with one commit (for example a README): GitHub's Git data
   API refuses every write to an empty repository. See [CI budget ledger](#ci-budget-ledger)
   for the rulesets it needs.
2. Create TWO GitHub Apps, so that a sandbox escape in the judge cannot forge a green check:
   - the **ledger App**: repository permission *Contents: read and write* only, installed on
     the ledger repository ONLY (not on the judged repository). Generate a private key. It is
     the only credential the judge job, which runs candidate code, can see;
   - the **checks App**: repository permission *Checks: read and write* only, no Contents,
     installed on the judged repository ONLY. Generate a private key. It is used only by the
     `post` job, which never runs PR code.

   Each workflow token narrows these permissions further (see
   [How it behaves](#how-it-behaves)).
3. On a trusted workstation, after `cheeseless freeze`, seed the ledger branch of the
   lineage **before enabling Ruleset B** (it blocks branch creation):
   `CHEESELESS_BUDGET_TOKEN=... cheeseless ci-budget-init --budget-repo OWNER/LEDGER
   --repo-id ID`, where `ID` is the numeric id of the judged repository
   (`GITHUB_REPOSITORY_ID`, also shown by `gh api repos/OWNER/NAME --jq .id`). The token
   needs *Contents: read and write* on the ledger repository only; the command removes it
   from its environment first and never prints it. It writes one parentless commit whose
   tree holds only `record.json`, MAC'd with the budget key derived from the store key,
   reads it back, and only then creates the branch. Stdout is the root commit SHA alone.
   Re-running is safe: a valid seed of this repository, lineage and store key is returned
   as is, and a branch that already has runs or belongs to anything else is refused.
4. On the same workstation, run
   `cheeseless ci-export --out PATH --budget-repo OWNER/LEDGER --budget-root ROOT_SHA`.
   The path must be outside the repository. The bundle is written with mode 0600 and never
   printed. It pins the ledger repository, the lineage branch and the root commit; a bundle
   without them (version 1) is refused, so re-export after upgrading.
   The bundle records how many disputes the lineage had at export. After any dispute (even a
   rejected one) it is stale: run `cheeseless ci-bundle-check --bundle-file PATH` (exit 1 when
   stale), then re-export and replace the CI secret. The judge has no store and cannot check this.
5. Create the environment `cheeseless-judge`. Under *Deployment branches and tags* choose
   *Selected branches and tags* and allow exactly the default branch, with no tag rule.
   Add no required reviewers (the judge runs unattended). In that environment store:
   - secret `CHEESELESS_CI_BUNDLE`: base64 of the bundle file, one line;
   - secret `CHEESELESS_LEDGER_APP_KEY` and variable `CHEESELESS_LEDGER_APP_ID`: the ledger
     App private key and its **Client ID** (not the numeric App id);
   - secret `CHEESELESS_CHECKS_APP_KEY` and variable `CHEESELESS_CHECKS_APP_ID`: the checks
     App private key and its **Client ID**;
   - variable `CHEESELESS_BUDGET_REPO`: the ledger repository **name** (no owner).

   Then delete the local bundle and key files. **Never store these as repository
   secrets**, and delete any repository-level `CHEESELESS_CI_BUNDLE` from an older setup:
   a pusher can read repository secrets from a workflow on their own branch, while
   environment secrets reach only jobs that name the environment and run on an allowed
   branch.
6. Copy `judge.yml` to `.github/workflows/judge.yml` on the default branch. Adjust the
   `workflows:` name to match your PR workflow. Keep `ci.yml`'s `run-name`
   (`ci judge=yes|no`): a pull request that is only **edited** (title or body, base
   unchanged) gets `judge=no`, is not routed, so it neither spends the CI budget nor
   publishes a check; the earlier result stands. It runs in its own concurrency group
   (`judge.yml` keys the group on head SHA, triggering event and `run-name`), so it never
   cancels an in-flight `judge=yes` run. `run-name` comes from the PR's own `ci.yml`: this rule
   is a budget optimisation, not a security control.
7. Require ONE check on the default branch: `cheeseless gate`. Add a single ruleset (or
   branch protection rule) that targets the default branch (required status checks are
   scoped by the PR's **base** branch, never by its head branch, so a per-`gate/*` rule can
   never match) with the required status check `cheeseless gate`, and pin its **expected
   source to the checks App**. Also enable **Require branches to be up to date before merging**: a verdict is judged
   against one default-branch tip, and `post` fails a gate whose judged base is no longer the
   current tip, so a stale pass cannot merge after the base moved. Without the pin, a check run with the same name posted by any
   workflow (the shared `github-actions` app) would satisfy it. Do not require
   `cheeseless judge` or `cheeseless branch policy`: they are published for information and
   are folded into the gate. The gate is class-aware:
   - `gate/<task>`: `success` only if the implementation judge PASSED and the branch policy
     passed;
   - `spec/<lineage>`: `success` if the branch policy passed. Its title and summary say it is
     **NOT an implementation verdict** (see the ledger's stage (b) rows; `<lineage>` is the
     task's stable authoring lineage id, ADR 0009's `spec/<task>`);
   - any other class, or any failure: `failure`.

   The gate is always `success` or `failure`. `neutral` and `skipped` are never used,
   because GitHub treats them as satisfying a required check.

## How it behaves

- Runs from the default branch (`workflow_run`), never from PR code. One workflow, five
  jobs:
  - `route` (no secrets) resolves the live PR from the head SHA and verifies its base, head,
    head repository (a fork PR is refused) and branch class with `cheeseless ci-route`.
  - `judge` runs only for `gate/<task>`. It fetches the PR objects and runs the sandboxed
    implementation judge. It holds only the budget token (see below), never the checks
    credential, and hands the verdict (with the `judged_base` it ran against) to `post` as an
    artifact and an exit-code output.
  - `policy` (no environment, no secrets) decides the branch class and path confinement
    (`cheeseless ci-branch --check-payload --no-author-check`) by tree comparison only. It
    fetches PR objects but executes none of their content. Its payload reaches `post` as a
    job output, which a sandboxed job cannot write.
  - `evaluate` runs for every validated PR, unprivileged and **advisory**: it runs the
    sandboxed author check, which executes PR acceptance content. Nothing it produces is
    consumed by `post`, and its result is only shown in the `post` log, never a guard.
  - `post` is the only job that can publish checks. It has the environment and the checks App key
    but checks out only the default branch and never fetches or runs PR code. It runs
    `cheeseless ci-gate`, which validates the policy payload (exact schema, head SHA, class),
    requires `judged_base` to equal the current default-branch tip (a stale verdict fails),
    requires every upstream result to be as the class needs (`policy` success;
    `judge` success for `gate/`, skipped otherwise) and builds the check bodies, re-checks that `pulls/N` still has the same head SHA, mints the
    checks token and posts `cheeseless gate` (required) plus the informational
    `cheeseless judge` (`gate/` only) and `cheeseless branch policy` checks.
  An author PR can never earn an implementation PASS. A PR that cannot be verified (not
  exactly one open PR, non-default base, fork head, head moved) publishes nothing, so the
  required check stays missing and the merge stays blocked.
- Resolves the PR from the head SHA, refuses a non-default base, fetches the head as objects
  only and never checks it out.
- A PR that touches `acceptance/` or other protected paths (measured from its merge base
  with the default branch) fails with no run and no charge.
  Protection applies to the net PR tree, not to commit history: a protected change that is
  reverted inside the PR is not flagged. Require squash merges for history-level protection.
- If the default branch's `acceptance/` differs from the frozen tree, `ci-judge` exits 3
  (`FREEZE_DRIFT`): re-freeze first.
- Exit codes: 0 PASS, 1 FAIL/BLOCKED, 2 usage or infrastructure error, 3 FREEZE_DRIFT.
  The check is `success` only for exit 0 with status `pass`; anything else is `failure`.
- Two App tokens, in two different jobs; the App key itself reaches only the mint steps:
  - the **budget token**, from the ledger App (Contents write, ledger repository only) is minted right before
    the judge step, is passed only to it as `CHEESELESS_BUDGET_TOKEN`, and is revoked in the
    step right after it, whatever the judge did. The judge removes it from its own
    environment before it builds any sandbox, and the candidate sandbox env is an allowlist
    that never includes it;
  - the **checks token**, from the checks App (Checks write only) is minted only in the `post` job, which has no
    PR code, and used only to post the checks.

  Both are revoked again at the end of their job. `GITHUB_TOKEN` stays read-only. The bundle
  is exposed only to the judge step.

## CI budget ledger

The CI-pool budget (`ci_limit` in the bundle) is enforced across runs. Each judge run that
reaches hidden execution appends one HMAC'd record to the lineage branch with a non-force,
fast-forward-only ref update, keyed by the workflow run id and attempt. A re-run is a new
attempt and a new charge. Failures before the hidden run (protected path, drift, build,
smoke) are never charged; once reserved, a charge is kept whatever happens. A missing,
malformed or foreign ledger blocks the run (`blocked`, reason `infra_error`), and so does a
charge that disappears from the chain before the judge's final read, whatever the verdict;
an exhausted budget blocks it with reason `budget_exhausted`. The judge logs the observed
count on every run.

The judge cannot detect a ledger rewound to an earlier valid commit: a replayed prefix of
the chain looks like a shorter history. Only Ruleset A below prevents rewinds, so treat a
drop in the logged count as an incident.

In the ledger repository, create two rulesets targeting `cheeseless/budget/**`:

- **Ruleset A, no bypass actors:** restrict deletions and block force pushes. This binds
  the ledger App too, so no token can rewind or delete a ledger branch.
- **Ruleset B, bypass = the ledger App only (mode "always"):** restrict creations and
  restrict updates, so nobody else can create or append.

Rulesets on private repositories need a paid plan. Verify both rulesets on your account
before relying on them (`planning/research/ci-budget-counter.md` section 6 lists the
release-gate checks).

Residual risk: admins and holders of the App key can alter or reset the ledger. A leaked
budget token cannot rewind, but it can append unsigned commits, which blocks every later run
of that lineage until an admin repairs the branch (a denial of service, not an inflated
count). The workflow pins the ledger owner to the judged repository's owner.

**Same-repository opt-out (not recommended).** Pointing `--budget-repo` and
`CHEESELESS_BUDGET_REPO` at the judged repository itself works, but a Contents-write token
can then create, update or delete every unprotected branch and tag of the judged repository.
If you choose it, default-branch protection must still bind the ledger App (do not make it
a bypass actor there), and rulesets A and B go into the judged repository.

The bundle HMAC is an integrity check, not authentication: protect the secret itself.
