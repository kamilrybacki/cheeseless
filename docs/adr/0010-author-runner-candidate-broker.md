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
Host broker, because one launcher code path gives an author call and a normal call the same
sandbox profile, fresh per-call namespaces and process-group kill. Requests carry only `argv`,
`stdin` (base64, bytes preserved; absent means /dev/null) and `timeout`; any other field is
refused. The tree is host-pinned to the bound reference. The socket lives in a private 0700
directory, bound read-only into that sandbox only; the candidate profile binds no socket.
Admission is bounded before any read: 8 connections, then closed. Candidate calls are a separate
cap of 4 concurrent. Aggregate call time (300 s) is reserved at admission, which is not suite
wall time. Per-call time is capped at 30 s. Reads have an absolute deadline.
With `--no-reference` no broker starts and every call is refused.
This extends ADR 0009 and does not edit it: its "reference mounted read-only" clause changes.
The reference is now absent from the author sandbox and reached only via the broker.

## Consequences
- ✅ Equivalent by construction: profile, namespaces, kill and deadline. Probed: markers, abstract
  sockets, permission and same-mtime changes, stdin bytes, suite /tmp output. Not claimed: timing.
- ✅ No candidate path, env, cwd or profile can be chosen by untrusted code.
- ⚠️ No peer-credential check. 0700 does not exclude other host processes with the same UID;
  residual risk under ADR 0001/0003.
- ⚠️ Every call pays a bwrap start, as in the normal path.
Author model: Sonnet 5.5 (`claude-sonnet-5-5`); reviewers must differ, one from another vendor.
