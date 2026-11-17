---
status: proposed
date: 2026-11-17
reviewers: []
---
# 0010. Reach the candidate from the author sandbox only through a host broker

## Context
The author runs the unapproved acceptance suite, which must be able to call the reference
candidate. The earlier design copied the tree inside the author sandbox and swept state
between calls. It was not equivalent to the normal per-call namespaces: markers survived in
/dev, /w, /run and new directories, an abstract-socket process outlived its call, metadata
snapshots missed permission and same-mtime content changes, and the sweep deleted suite output.
A host endpoint reachable from untrusted code is a new boundary not covered by ADR 0003/0009.

## Options
- **In-sandbox copy and sweep**: cheap, but a re-implementation that drifts from the normal path.
- **Host broker**: the suite sends a closed request; the host runs the normal launcher.

## Decision
Host broker, because one launcher code path makes an author call equal a normal call by
construction. Requests carry only `argv`, `stdin` and `timeout`; any other field is refused.
The tree is host-pinned to the bound reference. The socket lives in a private 0700 directory
bound only into that sandbox; the candidate profile still binds no socket. The host enforces a
per-call deadline, a concurrency cap and total call count and time tied to the 300 s suite
deadline. Each call kills its whole process group, and calls die with the author sandbox.
With `--no-reference` no broker starts and every call is refused.

## Consequences
- ✅ Author and normal observations are identical; the snapshot, sweep and copy are deleted.
- ✅ No candidate path, env, cwd or profile can be chosen by untrusted code.
- ⚠️ A new host endpoint exists; access control is directory privacy, not peer credentials.
- ⚠️ Every call pays a bwrap start, as in the normal path.
