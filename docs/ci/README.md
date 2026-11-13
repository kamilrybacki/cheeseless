# CI judge setup

The CI judge runs the FULL suite (visible and holdout) against a pull request head and
posts a check run with the aggregate status and visible-criterion statuses only.
`docs/ci/judge.yml` is a template; do not install it in this repository.

## Setup

1. On a trusted workstation, after `cheeseless freeze`, run
   `cheeseless ci-export --out PATH`. The path must be outside the repository. The bundle is
   written with mode 0600 and never printed.
2. Store the file contents as the repository secret `CHEESELESS_CI_BUNDLE`
   (base64 of the file, one line). Delete the local copy.
3. Copy `judge.yml` to `.github/workflows/judge.yml` on the default branch. Adjust the
   `workflows:` name to match your PR workflow.
4. Make the `cheeseless judge` check required in branch protection.

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
- The secret is exposed only to the judge step.

## CI budget is NOT enforced across runs

Each `ci-judge` run uses an ephemeral store, so the CI-pool budget counts one run per
invocation and is not enforced across runs (ledger P10-6). Limit abuse with GitHub-side
controls (required approvals for first-time contributors, concurrency groups).
The bundle HMAC is an integrity check, not authentication: protect the secret itself.
