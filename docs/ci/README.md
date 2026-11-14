# CI judge setup

The CI judge runs the FULL suite (visible and holdout) against a pull request head and
posts a check run with the aggregate status and visible-criterion statuses only.
`docs/ci/judge.yml` is a template; do not install it in this repository.

## Setup

1. Create a dedicated **private ledger repository** under the same owner as the judged
   repository (for example `cheeseless-ledger`). It holds the cross-run CI budget counter,
   one branch `cheeseless/budget/<lineage>` per lineage. Pushers of the judged repository get
   no access to it. See [CI budget ledger](#ci-budget-ledger) for the rulesets it needs.
2. Create a dedicated **judge GitHub App** with repository permissions *Checks: read and
   write* and *Contents: read and write*, install it on the judged repository and on the
   ledger repository only, and generate a private key. Each workflow token narrows these
   permissions further (see [How it behaves](#how-it-behaves)).
3. Seed the ledger branch of the lineage: one root commit with no parent whose tree holds
   only `record.json` and whose commit message equals that file. The content is the seed
   record from `planning/research/ci-budget-counter.md` section 3, MAC'd with the budget key
   derived from the store key. A `cheeseless ci-budget-init` command is planned but not yet
   shipped. Note the root commit SHA.
4. On a trusted workstation, after `cheeseless freeze`, run
   `cheeseless ci-export --out PATH --budget-repo OWNER/LEDGER --budget-root ROOT_SHA`.
   The path must be outside the repository. The bundle is written with mode 0600 and never
   printed. It pins the ledger repository, the lineage branch and the root commit; a bundle
   without them (version 1) is refused, so re-export after upgrading.
5. Create the environment `cheeseless-judge`. Under *Deployment branches and tags* choose
   *Selected branches and tags* and allow exactly the default branch, with no tag rule.
   Add no required reviewers (the judge runs unattended). In that environment store:
   - secret `CHEESELESS_CI_BUNDLE`: base64 of the bundle file, one line;
   - secret `CHEESELESS_JUDGE_APP_KEY`: the App private key;
   - variable `CHEESELESS_JUDGE_APP_CLIENT_ID`: the App client id;
   - variable `CHEESELESS_BUDGET_REPO`: the ledger repository **name** (no owner).

   Then delete the local bundle and key files. **Never store these as repository
   secrets**, and delete any repository-level `CHEESELESS_CI_BUNDLE` from an older setup:
   a pusher can read repository secrets from a workflow on their own branch, while
   environment secrets reach only jobs that name the environment and run on an allowed
   branch.
6. Copy `judge.yml` to `.github/workflows/judge.yml` on the default branch. Adjust the
   `workflows:` name to match your PR workflow.
7. Make the `cheeseless judge` check required in branch protection (or a ruleset) and pin
   its **expected source to the judge App**. Without the pin, a check run with the same name
   posted by any workflow (the shared `github-actions` app) would satisfy it.

## How it behaves

- Runs from the default branch (`workflow_run`), never from PR code.
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
- Two App tokens, never the App key, reach steps:
  - the **budget token** (Contents write, ledger repository only) is minted right before
    the judge step, is passed only to it as `CHEESELESS_BUDGET_TOKEN`, and is revoked in the
    step right after it, whatever the judge did. The judge removes it from its own
    environment before it builds any sandbox, and the candidate sandbox env is an allowlist
    that never includes it;
  - the **checks token** (Checks write only) is minted after the judge step and used only to
    post the verdict.

  Both are revoked again at the end of the job. `GITHUB_TOKEN` stays read-only. The bundle is
  exposed only to the judge step.

## CI budget ledger

The CI-pool budget (`ci_limit` in the bundle) is enforced across runs. Each judge run that
reaches hidden execution appends one HMAC'd record to the lineage branch with a non-force,
fast-forward-only ref update, keyed by the workflow run id and attempt. A re-run is a new
attempt and a new charge. Failures before the hidden run (protected path, drift, build,
smoke) are never charged; once reserved, a charge is kept whatever happens. A missing,
malformed, foreign or rewound ledger blocks the run (`blocked`, reason `infra_error`); an
exhausted budget blocks it with reason `budget_exhausted`. The judge logs the observed
count on every run.

In the ledger repository, create two rulesets targeting `cheeseless/budget/**`:

- **Ruleset A, no bypass actors:** restrict deletions and block force pushes. This binds
  the judge App too, so no token can rewind or delete a ledger branch.
- **Ruleset B, bypass = the judge App only (mode "always"):** restrict creations and
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
If you choose it, default-branch protection must still bind the judge App (do not make it
a bypass actor there), and rulesets A and B go into the judged repository.

The bundle HMAC is an integrity check, not authentication: protect the secret itself.
