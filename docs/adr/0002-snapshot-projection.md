---
status: proposed
date: 2026-11-13
reviewers: [gpt-6.1-terra]
---
# 0002. Give the agent a snapshot projection, not a git worktree

## Context
A git worktree shares the object database, config and remotes with the original repo,
so hidden tests stay reachable through `git show` or the original path (ledger Q16).
Tests also have to be readable by humans and live in the same repo (Q42).

## Options
- **Worktree**: cheap, but shares objects, history and remotes.
- **Snapshot projection**: a fresh single-commit repo with protected paths removed and no remote (Q16).
- **Encrypting smudge filter**: same protection, worse ergonomics, key needed for every git op (rejected, Q47).

## Decision
Snapshot projection. One repo with plaintext tests in git and on GitHub; no `hidden/`
subfolder: everything under `acceptance/` except `contract/` is hidden from the gated
agent (Q42). The local working tree does not materialise those paths: sparse-checkout removes them
and keeps them absent, `cheeseless doctor` checks for strays (Q47, ledger D5); the
verifier reads blobs from git into tmpfs.
Humans use `cheeseless show`/`edit` or GitHub (Q47).

## Consequences
- ✅ The agent's git objects contain no protected blobs; checked with a canary (Q29 criterion 1).
- ✅ Tests stay reviewable in one repo, no private companion repo (Q42).
- ⚠️ Sparse-checkout is local state: plain editors and `git status` need the `show`/`edit` commands.
- ⚠️ Plaintext tests are public, so memorisation by models is a residual risk (ADR 0001).
