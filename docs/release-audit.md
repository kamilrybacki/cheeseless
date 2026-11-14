# Release audit (phase 12)

The release audit is a **local, redacted, fail-closed** check of the three surfaces
that can leak secrets or private material:

1. the current tracked working tree,
2. every locally reachable git object, plus commit metadata, messages and paths,
3. the sdist and wheel built from the current tree.

It runs the official [Gitleaks](https://github.com/gitleaks/gitleaks) release locally.
Nothing is uploaded, no model or network service is contacted by the audit, and the
repository is never edited, deleted or rewritten. A finding only blocks the release.

## Install the pinned scanner

The audit requires exactly Gitleaks **8.30.1** and refuses any other version. The helper
downloads only the official GitHub release asset, verifies the published SHA-256 and
checks the binary:

```sh
uv run python scripts/install_gitleaks.py --dest "$HOME/.local/bin"
gitleaks version   # 8.30.1
```

The pin is:

| item | value |
|------|-------|
| release | `gitleaks_8.30.1_linux_x64.tar.gz` |
| archive SHA-256 | `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb` |
| extracted binary SHA-256 (linux x64) | `88f91962aa2f93ac6ab281d553b9e125f5197bbbce38f9f2437f7299c32e5509` |
| official docs | `https://github.com/gitleaks/gitleaks` |

The audit always hashes the scanner it runs. In the default mode a digest that does not
match the Linux/x86_64 pin, or a host whose platform has no pin, is recorded as a
report limitation and does not block. With `--release` either condition fails closed with
`gitleaks-digest-mismatch` (exit 2), so the operator's publication gate only accepts a
verified binary.

## Run

```sh
uv run python scripts/release_audit.py \
  --gitleaks "$HOME/.local/bin/gitleaks" \
  --report-dir "$HOME/.local/state/cheeseless/release-audit"
```

The operator's publication gate adds `--release` and an external denylist, and only
passes once the server-side refs have been attested:

```sh
uv run python scripts/release_audit.py \
  --gitleaks "$HOME/.local/bin/gitleaks" \
  --denylist "$HOME/.config/cheeseless/operator-denylist.toml" \
  --release --remote-refs-attested \
  --report-dir "$HOME/.local/state/cheeseless/release-audit"
```

If `--report-dir` is omitted the report goes under
`$XDG_STATE_HOME/cheeseless/release-audit` (or `~/.local/state/...`). The directory is
created `0700` and each report is `0600`. A report directory inside the repository is
refused.

The audit builds a real sdist and wheel with `uv build`. `--wheel FILE` and `--sdist FILE`
audit prebuilt artifacts instead; both must be supplied together.

## Release gate (`--release`)

`--release` is a strict gate for the operator, not for CI. It is checked after the
scanner identity is established and before anything is scanned:

- A missing or empty denylist (zero entries) fails closed with exit 2,
  `denylist-required`.
- A scanner binary whose digest is missing or does not match the pinned Linux/x86_64
  digest, or a host whose platform has no pin, fails closed with exit 2,
  `gitleaks-digest-mismatch`.
- The audit is otherwise **blocked** (exit 1) with a metadata-only finding
  `remote-refs-unattested`, unless `--remote-refs-attested` is also passed. That flag
  asserts that the operator verified, outside this tool, that server-side refs such as
  `refs/pull/*` no longer expose pre-rewrite commits. The audit cannot inspect those
  refs from a local clone, so it cannot verify the assertion itself.

The report JSON records `release_mode` and `remote_refs_attested`. Without `--release`
the behaviour is unchanged: the denylist stays optional and the server-side refs remain
a stated limitation. `--remote-refs-attested` only changes the result when `--release`
is set.

## What is checked

- **Secrets:** Gitleaks default rules, extended with one narrow exception (below), run on
  the tracked tree, the full local history (`git log --all`), an exported copy of every
  reachable commit message/author/committer field, every historical path and original local ref name, raw contents of
  every reachable blob and annotated tag object (bounded per object and in total), and
  each built archive after safe extraction. Historical paths are enumerated per commit
  (`git log --all --root -m --no-renames --name-only -z`) and from reachable object names, so
  identical blob content committed under both a clean and a sensitive name is checked
  under every name it ever had. Historical blob and tag content is staged and
  scanned independently of git patches, so a deleted binary blob or a tag message cannot
  escape the scan. Every scanner invocation uses `--ignore-gitleaks-allow`; committed
  inline exemption comments cannot suppress findings.
- **Versioned metadata:** commit messages, author/committer identities and filenames are
  written to a private staging file under a cap and scanned as content. This is separate
  from the patch scan and does not rely on Gitleaks reading commit messages.
- **Operator denylist:** an optional TOML file outside the repository adds operator-owned
  paths, email addresses or personal identifiers. Its literal values are never committed.
  The rules are applied to the tracked tree, every reachable blob and annotated tag,
  historical paths, and both the names and extracted contents of archive members. The
  report contains only the rule id and an opaque location; a matched substring is never
  emitted.
- **Archive hygiene:** absolute paths, `..`, backslashes, symlinks/hardlinks, devices,
  encrypted members, duplicate names, over-cap members, excessive nesting and unexpected
  `acceptance/` material are refused without reading the member. `.env`, auth files,
  credential stores and private state (caches, `evidence/`, `seed/`, `.cheeseless/`,
  virtualenvs, build output) are refused in archives and directly on tracked paths.
  Extraction is bounded and uses `O_NOFOLLOW` files. Member names are collected during
  the bounded extraction; an exhausted archive is never reopened to enumerate it again.
- **Packaging completeness:** every Python module under `src/cheeseless/` must be present
  in the wheel and in the sdist, so a cleanup that strips a trusted runtime module fails
  the audit.

The sdist/config exclude local caches, the `CLAUDE.md` convenience symlink and the
synthetic fixture itself. The audit does not add broad rule exclusions: the only
allowlist entry targets `generic-api-key`, and only when the exact synthetic fixture value
appears at the exact repository-root path `tests/fixtures/release_audit/synthetic-token.txt`.
Both conditions are required together (`condition = "AND"`), so a different credential at
that path, or the same value at another path, is still detected.

## Output and redaction

The scanner runs with `--redact`; its raw JSON report is deleted without being printed.
Each audited archive is identified in the report by its kind, an opaque archive name and
its SHA-256.
The tool emits only metadata: scanner, rule id, an opaque location, historical commit,
line and count. Locations are a short SHA-256 of the source name plus a safe category
(for example `tracked-file:…`, `archive-member:…`, `scanner-file:…`), and local refs are
reported the same way. Neither stdout/stderr nor the private JSON report contains a raw
tracked path, archive member name, ref name or Gitleaks `File` field. It never prints a
secret, private key, raw match or scanner stderr. The private JSON report has no `Secret`
or `Match` field and no absolute repository path; it records `release_mode` and
`remote_refs_attested` alongside the findings.

Exit status:

| code | meaning |
|------|---------|
| `0` | clean |
| `1` | blocked by one or more findings |
| `2` | fail-closed infrastructure error (scanner missing/incompatible/failed, unpinned scanner digest in release mode, missing/empty denylist in release mode, invalid report, unsafe/unreadable archive, shallow history, report/denylist location bad) |

## Operator denylist format

```toml
[[entries]]
id = "operator-home-path"
pattern = '''/home/[A-Za-z0-9._-]+/'''

[[entries]]
id = "operator-private-email"
pattern = '''operator@example\.invalid'''
```

The file must stay outside the repository. Rule ids are lower-case `[a-z0-9-]`, patterns
are Python regular expressions, and the audit caps the file size, entry count and pattern
length.

## Local refs and the history limit

The report enumerates every ref present in the local clone (including local branches,
remote refs and tool-specific refs) as an opaque identifier and states the limits
explicitly:

- Server-side pull refs and remote-only history cannot be enumerated from a clone.
- GitHub `refs/pull/1` and `refs/pull/2` are known to reference pre-rewrite commits that
  still carry the old author email. This audit cannot inspect or remove them. GitHub
  Support or a fresh repository is required; **do not claim the GitHub history is
  sanitized**. `--release` therefore blocks with `remote-refs-unattested` until the
  operator passes `--remote-refs-attested`; the flag records an operator verification of
  those server-side refs, it does not perform one.
- Gitleaks is a heuristic detector, not proof of absence.
- A shallow clone is refused because its history is incomplete. Replacement refs and
  graft files are also refused; every Git process, including scanner subprocesses,
  receives `GIT_NO_REPLACE_OBJECTS=1`.
- ZIP archive/member comments and extra fields, TAR physical headers, PAX fields and
  GNU long-name/link records are staged privately and scanned by both detectors.
- Before standard archive parsing, a bounded physical TAR walk counts extension
  records as members and caps metadata and decompressed bytes. ZIP preflight checks
  the end record and walks the bounded central directory before `ZipFile` constructs
  entries. Parser resource-limit hits fail closed with exit 2.
- Only ordinary ZIP and uncompressed/gzip TAR are supported. ZIP64, split ZIP,
  inconsistent ZIP offsets, trailing ZIP data and old GNU sparse TAR are refused. Metadata consumes
  the shared extraction byte budget; physical TAR metadata is additionally capped
  by the per-member byte limit. Conservative limits may reject otherwise valid
  packages. Archives are never executed.

## CI

`.github/workflows/ci.yml` has a `release-audit` job. It checks out full history, installs
the pinned scanner with the checksum check, builds the archives and runs the same redacted
audit. It is separate from the normal test job and does not receive repository secrets.

## Gate status

The tool and its tests are implemented. A local audit on 2026-11-14 found the tree, local
history and actual built archives clean with Gitleaks 8.30.1. That is **not** a claim of
release readiness: the live GitHub `P10-6` gate (P1 acceptance plus the thirteen live
checks), the server-side pull refs, explicit operator publication approval and the opt-in
real-agent E2E remain open. See [`../planning/handoff.md`](../planning/handoff.md).

After the six P12-6 review fixes, the real local audit on 2026-11-14 returned
**BLOCKED (exit 1)** with 13 `generic-api-key` finding records over 174 local refs
and two freshly built archives. The earlier clean result above predates these fixes.
The stricter audit result is recorded without broadening the exact fixture allowlist
or rewriting history. Full tests passed (1212 passed, 2 skipped, 87.79% coverage);
Ruff, formatting, mypy and all four import contracts passed. These results do not
establish release readiness.
