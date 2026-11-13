# AGENTS.md

Instructions for any coding agent working on **cheeseless**: a git-native, black-box
quality gate that stops coding agents from editing or gaming acceptance tests.

## Language

Code, docs, ADRs, commit messages and CLI output are in English.

## Architecture rules

- Hexagonal layout under `src/cheeseless/`: `domain/` (pure types, no I/O),
  `ports/` (Protocols), `app/` (use cases), `adapters/inbound` (CLI, MCP, UDS server),
  `adapters/outbound` (git, sandbox, pytest runner, config, result store), `bootstrap.py`.
- `proto/cheeseless/v1/` is the **external** contract. Generated proto classes never
  enter `domain/` or `app/`; inbound adapters map proto ↔ domain types.
- Layer boundaries are enforced by `import-linter` in CI. Do not weaken a contract to
  make a change pass; change the design instead.
- The verifier never imports candidate code. Candidates always run behind a process
  boundary.

## Documentation rules

- `docs/adr/` holds only critical, long-lived decisions. Working material
  (implementation plan, ledger, research) lives in `planning/`.
- Every ADR uses [`docs/adr/template.md`](docs/adr/template.md), fits in 40 lines and is
  immutable once accepted. To change a decision, write a new ADR and mark the old one
  `superseded by ADR-NNNN`.
- **Creating or changing an ADR requires a cross-model review before it is accepted.**
  The author's model may not review its own ADR. Use as many meaningfully different
  models as are available (different vendors count more than different sizes of one
  family); at least one reviewer must come from another vendor. Record the reviewer model
  ids in the ADR's `reviewers` field and address or explicitly reject each finding in
  `planning/ledger.md`.
- Add each new ADR to the index in `docs/adr/README.md`.

## Security rules

- Never commit secrets, tokens, host names or personal infrastructure details. This repo
  is public; personal integrations belong in the user's own dotfiles.
- Changes to anything that protects hidden tests (projection, sandbox, disclosure filter,
  promote path filter) need a security test in `tests/security/` that proves the
  protection, including the canary check.

## Workflow

- TDD: write the failing test first. Keep coverage at 80% or more.
- Conventional commits (`feat:`, `fix:`, `docs:` …).
- Record non-trivial decisions that are not ADR-worthy in `planning/ledger.md`.
