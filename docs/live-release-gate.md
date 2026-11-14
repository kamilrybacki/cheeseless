# Live GitHub release gate

Runs the 13 release-gate checks of `planning/research/ci-budget-counter.md` section 6
against disposable private canary repositories. Nothing here is run in default CI.

## What is manual and what is automated

- **Manual, one click:** create and install the judge GitHub App. `manifest --apply`
  prints one local URL; open it and confirm on GitHub, then install the App on the ledger
  and judged canary repos (the script prints the install link).
- **Manual, tokens:** a pusher token (push access to the judged and ledger canary repos,
  nothing more) and an admin token for setup. Export them yourself; they are never printed.
- **Automated:** repos, environment `cheeseless-judge` (default branch only), rulesets A
  and B, canary secret, required check pinned to the App, canary workflows, and all checks.

## Run order

1. `uv run python scripts/live_gate_setup.py manifest --owner OWNER --key-file PATH`
   (add `--apply`). `PATH` must be outside any git repository; it is created at mode 0600.
2. Install the App on the canary repos, note its id and the installation ids.
3. `uv run python scripts/live_gate_setup.py setup --owner OWNER --app-id ID --apply`
   Without `--apply` every step is a dry run. Repeat runs are safe.
4. Export the variables below (setup prints the repo names).
5. `CHEESELESS_LIVE_GITHUB=1 uv run --frozen pytest tests/live -m live_github --no-cov`
6. `uv run python scripts/live_gate_setup.py teardown --owner OWNER --apply`
   Deletes only repos named `cheeseless-canary-*`.

## Environment variable names

`CHEESELESS_LIVE_GITHUB` (set to `1`), `CHEESELESS_LIVE_APP_ID`,
`CHEESELESS_LIVE_APP_KEY_FILE`, `CHEESELESS_LIVE_LEDGER_INSTALLATION_ID`,
`CHEESELESS_LIVE_JUDGED_INSTALLATION_ID`, `CHEESELESS_LIVE_PUSHER_TOKEN`,
`CHEESELESS_LIVE_ADMIN_TOKEN`, `CHEESELESS_LIVE_LEDGER_REPO`,
`CHEESELESS_LIVE_JUDGED_REPO`, `CHEESELESS_LIVE_FRESH_REPO`,
`CHEESELESS_LIVE_CANARY_SECRET` (the secret **name**), and optionally
`CHEESELESS_LIVE_SAME_REPO` (check 8; otherwise it reports not applicable).
Repo names are `OWNER/NAME`. The suite never reads a secret value into output.

## Reading the results

- A missing prerequisite reports `precondition missing` and the test fails; it never passes
  silently. Only check 8 without a same-repo config is skipped.
- **Plan caveat:** rulesets on private repositories need a plan that supports them.
  Check 1 reports it; if rulesets are unavailable, checks 2-5 cannot pass.
- **404 is not a rejection.** A pusher without ledger access sees 404, so the pusher needs
  push permission on the ledger repo; otherwise the result is a missing precondition.
- **Expected actor outcomes:** the budget token is the judge App, the Ruleset B bypass
  actor, so its creation and fast-forward are accepted. Pusher writes are rejected, and
  delete, force-push and rewind are rejected for everyone (Ruleset A).
- Checks 9-11 need real Actions runs (minutes). Check 11's `mergeable_state` signal has not
  been observed live; treat its first real result with suspicion.

## Offline tests

`uv run --frozen pytest tests/unit/test_live_checks.py tests/unit/test_live_setup.py`
prove the decision logic and the setup script against fakes, with no network.

Setup records repository full names, numeric IDs and creation ownership in
`.cheeseless-live-ownership.json` (override with `--ownership-manifest PATH`). Keep this
non-secret file for teardown, and use the same owner, suffix and `--same-repo` setting.
Existing repositories are recorded as unowned; rerunning setup preserves ownership only
when the numeric ID still matches. Creation is recorded before later setup steps, so a
partial setup can still be cleaned up. Missing ownership records authorize no deletion.
Teardown requires `--apply`, deletes only configured canary repositories recorded as
created by setup, and verifies their numeric IDs immediately before deletion. An ID
change refuses teardown; an unconfigured `same` repository is never deleted. Protect the
manifest from modification by other users: it is the local ownership record.

The App manifest code is exchanged in-process over verified HTTPS with a timeout;
it never enters subprocess arguments, environment variables or diagnostic output.

Protection probes first prove that the same credential can write to an unprotected
control branch, then remove that branch and its file. Authentication failures and
unattributable conflicts are missing preconditions. Matrix cells use independent refs
and require admin-readable rule-suite evidence: the same ref and authenticated actor,
a `fail` result, and a timestamp at or after the attempt started (GitHub timestamps
have second precision; probe refs are run-unique). Suites are polled within the check's
budget because publication can lag. A `bypass` result cannot prove rejection.
Git-data rejections can say only `422 Reference update failed`, so message wording
does not establish protection. Check 8 instead requires an admin-readable classic
branch-protection configuration with `enforce_admins`, a successful same-token control
write, and a protected-write rejection in 403/409/422. Successful protected writes fail
the check; missing evidence remains a missing precondition. Secret isolation requires a successful runner control and
successful secret-absence steps; cancellation or infrastructure failure cannot pass.
Required-check spoofing verifies a successful GitHub Actions check on the exact SHA
before measuring mergeability, and the required context/App ID pin uses exact equality.
