# Native cheeseless tab in the T3 right panel (design)

Status: implemented (2026-11-26). Phase 0 is in cheeseless (#44); phases 1-4 are merged in the
fork. Phase 5 (a real `cheeseless dev --panes` run through the tab) is not done yet. The
sections below keep the original design; section 10 and the fork's
`apps/server/src/cheeseless/security-canary.md` override them where they differ. Scope: a fork-only T3 feature
(`t3code` fork, branch `feat/command-terminals`, merged to the fork's main) plus the small
cheeseless changes it needs. Contracts used: `docs/t3-phase-view.md`, ADR 0019,
`docs/schemas/dev-event-v1.json`. Today the operator opens three project scripts in the bottom
drawer; the user wants everything in one T3 tab instead.

## 1. Goal and non-goals

The tab shows, for one lineage of one project:

1. A phase header from `dev-events`: stage, actor, awaiting, reference, open disputes.
2. Live view-only panes for `coder` and `reference`.
3. An interactive `test-writer` pane, isolated: no history on disk, no "Add to chat", no copy
   into any thread context, and unreachable by an agent through T3 MCP tools or other
   agent-facing surfaces.

Non-goals: the tab never runs a phase transition, `run_dev_resume` or any write command
(`awaiting` is a prompt for a person, per the phase-view rules). No agent runs inside the tab.
No change to the sandbox or relay boundary (ADR 0003, 0009, 0014, 0019).

## 2. Findings that shape the design (verified in the fork)

- `TerminalManager.open` (`apps/server/src/terminal/Manager.ts`) already gives a command
  terminal what we need: `persist = input.command === undefined && input.persistHistory !== false`
  (~L2919), so it never writes history; it launches exactly its argv with no `$SHELL` fallback;
  a plain attach to a gone ephemeral terminal returns null.
- That guarantee is **in-memory state** (`ephemeralLaunchKeys`). After a restart a plain attach
  with a `cwd` can create a shell in that slot (the "restart gap" in `docs/t3-phase-view.md`). The
  old pty dies with the server, so no old output survives, but the slot becomes a shell.
- Terminals are keyed by `threadId + terminalId`, both **client-chosen**
  (`packages/contracts/src/terminal.ts`). `terminal.open/attach/observe/write/resize` are generic
  RPCs (`apps/server/src/ws.ts` ~L3046-3064). Any authenticated client that can name a terminal
  can read it.
- Even an ephemeral session keeps an in-memory `BoundedTerminalHistory` for resync.
- The right panel has a `terminal` surface kind and a per-thread surface list
  (`apps/web/src/rightPanelStore.ts`). A new `cheeseless` kind fits that union.
- "Add to chat" is gated client-side by `terminalKeepsHistory`
  (`apps/web/src/terminalLaunchFlags.ts`, fails closed). It is a UI gate only.
- The T3 MCP toolkits (`apps/server/src/mcp/toolkits/*`: attachment, device, environment, html,
  orchestrator, preview, previewControls, project, pullRequests, thread, worktree) have **no
  terminal read or write tool**. Terminal output is reachable only through the WS RPC. Agents can
  still reach thread transcripts, attachments, preview and device screenshots, and
  `t3_project_update`, whose input spreads `ProjectUpdatePayload.fields` (checked in
  `project/tools.ts`), so it **can write project `scripts`** and would write any new project
  field too. That matters for config (section 4).
- `ThreadId` is only a trimmed non-empty branded string (`baseSchemas.ts`), so
  `cheeseless:<projectId>` decodes. `Manager.open` takes `cwd` from its input and has no thread
  or project row lookup, so the server supplies the workspace root itself (checked; spike still
  confirms it end to end).
- `dev-events` must run inside a git repository (exit 2 otherwise), so the child's cwd is the
  project workspace root.

## 3. Decision: server-owned feeds, dedicated RPCs, reserved namespace

The T3 server runs `dev-events` as a **child process** (no pty) and the three role panes as
**command terminals** in a reserved terminal namespace. The client never sends argv, a path or
any string that is executed.

| Piece | Mechanism | Why |
| --- | --- | --- |
| Phase header | server child `cheeseless dev-events --lineage L --follow` | It is NDJSON, not a screen. A pty adds echo/escape handling and a history surface for no gain. The server decodes each line against the closed schema and pushes typed state. |
| `coder`, `reference` | command terminal: `cheeseless pane-relay --role R --lineage L` | Reuses pty, resize and xterm streaming. The relay is view only on the host side. |
| `test-writer` | same, interactive | Needs the real pty path for keystrokes. |

Rejected:

- **Project scripts and the bottom drawer.** Works today, but the user wants one tab, and project
  scripts are MCP-writable (`t3_project_update`), so an agent could redefine them.
- **Client calls `terminal.open` itself.** argv and ids come from the browser, generic
  observe/attach expose the pane, the restart gap stays open.
- **Panes as plain child processes over a custom stream.** Re-implements pty, resize and flow
  control. Only worth it if the Phase 2 spike shows the Manager cannot host the reserved ids.

### 3.1 Reserved namespace

Pane terminals use `threadId = "cheeseless:<projectId>"` (no real thread has that id) and
`terminalId = "role-<role>"`. No thread transcript, attachment store or drawer references them.

The Manager gets one guard for every generic terminal RPC: if `threadId` starts with
`cheeseless:`, then `open`, `attach`, `observe`, `write`, `resize`, `clear` and `close` are
**refused** unless the call comes from the cheeseless module through an internal service call.
Generic RPCs then cannot read or re-launch a pane, and a plain attach after a restart cannot make
a shell there. This closes the restart gap in code.

### 3.2 Lineage discovery

Order: (a) explicit per-project config `lineage`; (b) otherwise the latest lineage from a stable
listing command (section 7); (c) otherwise the tab shows "no lineage" and polls the listing every
5 seconds. "Latest" is only a default; the operator can pick another from the listing. The tab
never builds a lineage from free text. A lineage is validated against cheeseless lineage-id rules
in the contract before use.

## 4. Contracts and RPC additions (`packages/contracts`)

New `packages/contracts/src/cheeseless.ts` (Effect schemas), re-exported from the index:

```ts
export const CheeselessRole = Schema.Literals(["test-writer", "reference", "coder"]);
// stage, actor, awaiting, reference, hint: one Schema.Literals() each, copied from dev-event-v1.
export const CheeselessPhase = Schema.Struct({
  lineage: LineageId, stage, actor, awaiting, reference,
  holdoutFailures: Schema.Int, openDisputes: Schema.Int, stateVersion: Schema.Int,
  hint: CheeselessHint,                 // a key, never run
  observedAt: IsoDateTime, ended: Schema.Boolean, errored: Schema.Boolean,
});
export const CheeselessPaneInput = Schema.Struct({
  projectId: ProjectId, lineage: LineageId, role: CheeselessRole }); // no argv, path or cwd
export const CheeselessPaneSummary = Schema.Struct({
  role: CheeselessRole, status: TerminalSessionStatus, interactive: Schema.Boolean });
export const CheeselessLineageList = Schema.Array(Schema.Struct({ lineage: LineageId, stage }));
```

Every enum is copied from the schema file, with a **contract test that diffs it against
`docs/schemas/dev-event-v1.json`**, so a cheeseless enum change fails T3's build instead of
rendering garbage. A line that fails decoding is dropped (viewer rule 1).

Project config: an optional `cheeseless` block on the project, `{ enabled, lineage?, stateDir?,
executable? }`. It is settable only through a UI RPC, **not** through `ProjectCreatePayload` or
`ProjectUpdatePayload`, so not through `t3_project_update` (an agent must not repoint
`executable` or the state dir). `executable` defaults to `cheeseless` resolved from PATH once at
startup and must be absolute after resolution.

New RPCs in `packages/contracts/src/rpc.ts` (`WS_METHODS`):

| Method | Kind | Purpose |
| --- | --- | --- |
| `cheeseless.subscribePhase` | stream | typed `CheeselessPhase` updates; the server owns the child |
| `cheeseless.listLineages` | request | fixed-schema lineage list |
| `cheeseless.openPane` | request | start or reuse a pane; returns `CheeselessPaneSummary` |
| `cheeseless.paneStream` | stream | output frames for one role, with in-memory resync |
| `cheeseless.paneWrite` | request | keystrokes; accepted only for `test-writer` |
| `cheeseless.paneResize` | request | accepted only for `test-writer` |
| `cheeseless.closePane` | request | drop the client view and the in-memory buffer; stops nothing on the host |

`paneWrite` and `paneResize` for `coder` and `reference` return a fixed error. This duplicates
the host relay's rule on purpose (defense in depth).

## 5. Server module and web components

Server, `apps/server/src/cheeseless/`:

- `CheeselessConfig.ts`: reads the project block, resolves the executable, validates lineage.
- `PhaseFeed.ts`: spawns `dev-events --follow --interval 1.0` with argv (no shell), reads stdout
  line by line, decodes, drops bad lines, keeps the latest view, stops on `end`, `error`, exit or
  last subscriber. Restarts with backoff if the process dies without `end`.
- `PaneService.ts`: builds argv **from the enum role and the validated lineage only**, calls an
  internal `TerminalManager.openCommand` with `persistHistory: false`, the reserved ids and a
  scrubbed env (PATH, HOME and the cheeseless state dir; no T3 auth tokens). Owns the
  write/resize policy.
- `handlers.ts`: maps the RPCs in `apps/server/src/ws.ts` next to the terminal handlers.
- `terminal/Manager.ts`: the namespace guard (3.1) plus the internal `openCommand` entry.

Web, `apps/web/src/`:

- `rightPanelStore.ts`: add `{ id: "cheeseless"; kind: "cheeseless"; lineage?: string }` to
  `RightPanelSurface`. `RightPanelTabs.tsx` adds the tab; `chat/RightPanelSurfaceContent.tsx`
  routes it.
- `components/cheeseless/`: `CheeselessPanel.tsx`, `PhaseHeader.tsx`, `RolePane.tsx`,
  `phaseLabels.ts`, `useCheeselessPhase.ts`, `useCheeselessPane.ts`.
- `RolePane` reuses the xterm view. It lives inside `ThreadTerminalDrawer.tsx` today; phase 3
  extracts a `TerminalView` that takes a stream and a write callback and has **no** add-to-chat,
  selection-to-context or history prop. This is the largest web risk (open question 2).
- Labels: `phaseLabels.ts` maps each enum to a literal string through an exhaustive `Record`
  (TypeScript fails on a missing member). `hint` shows as its label only. `awaiting` shows "run
  this in your own terminal" with a fixed line per member. Nothing from the store is
  interpolated, and the header never offers a button that runs a command.
- The `test-writer` pane closes and its buffer drops on unmount, project change, or tab hidden
  longer than a grace period. Nothing from a pane goes to `localStorage`
  (`terminalUiStateStore.ts` is not used for it).

## 6. Leak paths for test-writer output

"Code" = closed by a change in this design. "Accepted" = residual risk, stated and not fixed.

| # | Path | Closure | Status |
| --- | --- | --- | --- |
| 1 | History on disk | `persistHistory: false` forced by `PaneService`; reserved namespace; test scans the data dir for a canary | Code |
| 2 | Add to chat, terminal-context draft | `TerminalView` has no such prop; the surface never calls `terminalContext`; `terminalKeepsHistory` fails closed as a second gate; `paneStream` has no history field | Code |
| 3 | Operator copies text and pastes it into a chat | A human can paste what they see. Remove the context-menu entries only | Accepted |
| 4 | Generic terminal RPCs (`observe`, `attach`) | Namespace guard refuses them for `cheeseless:` ids. `paneStream` and `paneWrite` are bound to the WS connection that called `openPane`, one subscriber per pane; another connection is refused. A same-UID agent holding the T3 token can still open its own pane connection, so the residual exposure equals row 8 | Code, residual in row 8 |
| 5 | T3 MCP tools (`t3_thread_read`, `t3_thread_search`, attachments) | No terminal MCP tool exists (verified); output never enters a thread, attachment or message; the synthetic id has no thread. A test enumerates MCP tools and fails if a new terminal-reading one appears | Code |
| 6 | `t3_project_update` repoints executable or lineage | The `cheeseless` block is absent from MCP-writable payloads | Code |
| 7 | Preview tools (`preview_snapshot`, `preview_evaluate`, `preview_recording_start`, `device_screenshot`) | Primary: refuse preview navigation to T3's own origin, so these tools never see the shell UI. Secondary: render xterm on canvas, so a DOM snapshot holds no text (pixel capture is not stopped by this). Verify in phase 4 | Code (verify) |
| 8 | OS-level capture by a computer-use agent; same-UID agent reading the T3 auth token or memory | Same-UID agents are already out of scope (ADR 0019) | Accepted |
| 9 | Logs and telemetry (`RpcInstrumentation`, `FeatureUsage`) | They log method names and counts. `paneWrite` carries keystrokes, so exclude `cheeseless.pane*` payloads from instrumentation and structured logs; canary test over every log sink | Code |
| 10 | Restart gap | Guard refuses generic open/attach in the namespace; the pty dies with the server | Code |
| 11 | Server in-memory resync buffer | Bounded, dropped on `closePane`, never serialised, excluded from diagnostics dumps | Accepted |
| 12 | argv visible in the process list | Holds only role and lineage, no test content | Accepted |
| 13 | Browser memory and devtools on the operator's machine | The operator already owns the host | Accepted |
| 14 | `coder` and `reference` panes | They hold no hidden tests (ADR 0003, 0009, 0019). Same namespace and view-only rules, not the secrecy rules. The coder pane shows visible tests and code, so it also stays out of any thread | Code |

## 7. What cheeseless needs

1. **Lineage listing** (new, required). `cheeseless dev-lineages [--state-dir D]`, host only,
   read-only, not on MCP or UDS. One closed NDJSON line per lineage: `{v, lineage, stage,
   state_version, observed_at}`, newest first, with `docs/schemas/dev-lineage-v1.json`. Same
   encoder rules as `dev-events` (enums and integers, no free text). Exit 0 with no output when
   there are none; 2 outside a git repo.
2. **`pane-relay --wait SECONDS`** (new, recommended). Waits for the role socket and exits 3 on
   timeout, so the tab can open panes before the host announces them. Without it the server must
   retry on exit.
3. **Document `pane-relay` exit codes** in `docs/t3-phase-view.md` so `PaneService` can tell "no
   socket yet" from "host ended".
4. No change to `dev-events`, its schema, the sandbox or the relay protocol.
5. A new ADR is needed only if the lineage listing becomes an external contract; it then follows
   `docs/adr/template.md` and the cross-model review rule in `CLAUDE.md`. Record the rest in
   `planning/ledger.md`.

## 8. Phased tasks and tests

Each phase is TDD (failing test first) and ends green before the next.

**Phase 0, cheeseless.** `dev-lineages`, its schema, `pane-relay --wait`. Tests: ordering, empty
result, bad state dir, exit codes, schema conformance, and a `tests/security/` test that the
listing prints no path, digest, session id or note text.

**Phase 1, contracts.** `cheeseless.ts`, RPC entries, project block. Tests: schema round trips;
the enum-drift test against `dev-event-v1.json`; MCP project payloads reject the `cheeseless`
field.

**Phase 2, server.** `PhaseFeed`, `PaneService`, Manager guard, handlers. Tests: a fake
`cheeseless` binary emitting NDJSON (bad line, `end`, `error`, crash and restart); argv built only
from enums; `persistHistory` false and no history file; generic `terminal.open/attach/observe/
write` on a `cheeseless:` id refused, including with a fresh Manager (simulated restart);
`paneWrite` and `paneResize` refused for view roles.

**Phase 3, web.** Store kind, tab, `PhaseHeader`, `RolePane`, `TerminalView` extraction. Tests:
label-table exhaustiveness (type test); every enum rendered with a hostile `lineage` string and
unknown fields dropped; no "Add to chat" control, context-menu entry or `terminalContext` call in
the surface; unmount drops the pane; browser layout tests.

**Phase 4, security test (blocks merge).** A fake `test-writer` relay emits a canary and a
simulated operator types one. Assert neither appears in: any file under the T3 data and log
directories; every thread message and attachment; the output of every T3 MCP tool (an enumerated
list, so a new tool fails until reviewed); generic `terminal.*` RPCs (refused); RPC, structured
and telemetry log sinks; the DOM/preview snapshot of the tab; or a post-restart state. Mutation
check: removing any one closure from rows 1, 2, 4, 5, 9 or 10 must make the test fail.

**Phase 5, integration.** Run a real `cheeseless dev --panes` lineage in a scratch repo through
the tab; check all three panes, reconnect, server restart and tab close. Update
`docs/t3-phase-view.md` to name the tab as the preferred path and list the accepted risks.

## 9. Decisions by the operator (2026-11-20)

1. Lineage: per-project `lineage` config wins; the latest active lineage is the fallback, with a
   switcher in the tab.
2. The project `cheeseless` block is settable only through the T3 UI RPC, never by agents
   (`t3_project_update`, project create) or by repo files.
3. The test-writer pane stays open when the tab is hidden; the operator closes it.
4. Still open (technical): can the xterm view be extracted from `ThreadTerminalDrawer.tsx`
   unchanged? Phase 3 starts with a spike; fallback is a thin second xterm wrapper.

## 10. Cross-vendor review (gpt-6.1-sol, 2026-11-20) and resolution

These resolutions override the sections above where they differ.

1. **Critical, agent-installed setup scripts reach the relay.** An MCP agent can write project
   `scripts` and trigger a worktree setup that runs an ordinary shell, which can connect to
   `test-writer.sock` (the relay checks only the UID). This is the trusted-host residual of ADR
   0001/0017/0019: a same-UID host agent already reaches the store. The new part is that T3 acts
   as a deputy for agents without a shell. Resolution: for a project with the `cheeseless` block,
   the MCP project update and create paths refuse changes to `scripts` and `workspaceRoot`, and
   a security test proves it. Accepted residual: a host agent with a shell (ADR 0019 ⚠️).
2. **High, preview capture.** Blocking navigation is not enough. Every preview capture and
   automation operation (snapshot, evaluate, screenshot, recording) checks every frame's current
   URL against all T3 origin aliases and refuses on a match, on both the server and the desktop
   hosts. Phase 4 adds screenshot and recording canary tests. Canvas rendering stays as defence
   in depth only.
3. **High, `workspaceRoot` is agent-writable.** Handled by fix 1. The protected block also
   stores the canonical workspace root the operator approved; on a mismatch the server stops
   feeds and panes and the tab asks the operator to re-approve.
4. **Medium, pane identity.** The terminal key becomes `(project, lineage, role, generation)`.
   A lineage switch revokes subscriptions, destroys the terminal and its buffer, and rejects
   delayed writes or closes for an old generation.
5. **Medium, the `cheeseless:` prefix is not reserved.** Thread creation and import reject
   that prefix, and startup refuses existing records that use it.
6. **Medium, the environment is inherited.** Add a replacement-environment launch mode to
   `Manager` (an allowlist, not an overlay) for relays and feeds, and test the environment
   actually spawned.
