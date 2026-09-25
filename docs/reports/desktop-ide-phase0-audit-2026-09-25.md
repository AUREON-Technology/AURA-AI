# Desktop IDE Phase 0 audit (2026-09-25)

**Update (2026-09-25):** the layout-persistence and resize gaps this audit found in §1 (Shell /
State / Theme / Routing) are fixed — `LayoutNotifier` now loads/saves per-workspace layout via
`desktopLayoutState` (widths debounced, visibility/panel-switch toggles immediate), and
`DesktopShell` has real drag-to-resize handles (`aura_client/lib/desktop/shell/resize_handle.dart`)
between the sidebars/bottom panel wired to clamped setters. See
`aura_client/test/desktop/layout_notifier_test.dart` for coverage; unverified by a real run or CI
for the same toolchain reasons as the rest of this document. §1's table entries for these two rows
are left as originally written below (that's what Phase 0 found), with this note as the record of
what changed since.

**Update (2026-09-25, later the same day):** an unrelated-looking commit (`35f0371`, "Update
backend URLs across the project to use the new domain") bundled in real work on three more §2/§4
gaps. Re-verified against the current tree, each read fully (not diffed):

- §2 Explorer access-denied UI ("Grant Access"): now real — `explorer_panel.dart`'s `_ErrorView`
  branches on a real `isAccessDenied` check and wires a `Grant Access` button to a real
  `HostdControlClient.grantRoot` call. But it's gated on the wrong condition for the audit's
  original scenario: `explorer_service.dart`'s `listDirectory` silently falls back to `dart:io` on
  any hostd failure (including "this root was never granted"), so the case this UI was meant for
  — opening a workspace hostd has no grant for — still won't show it; it only fires for a
  containment violation or a genuine OS permission error. **Fixed in a later session (same day)**:
  `listDirectory`/`readFile` now take a `hostConnected` flag and only fall back to `dart:io`
  silently when hostd is actually unreachable (the D2-sanctioned "hostd down, read-only" case);
  a reachable-but-ungranted root now returns `isAccessDenied: true` instead. Fixing this exposed a
  second, more fundamental bug the naive gate would have made worse: the granted-root match
  compared `relativePath` directly against each granted root, and the workspace root's own
  top-level listing always calls this with `relativePath: ''` — which can never `startsWith` a
  non-empty granted root, so a *properly-granted* workspace's own top-level listing could never
  reach hostd at all. Without also fixing that (`_workspaceGrantCheckPath` now resolves an empty
  `relativePath` to the workspace root before matching), gating the fallback on `hostConnected`
  would have made *every* workspace show "Grant Access" as soon as hostd connects, granted or not
  — confirmed by reading `aura_hostd/lib/src/control_server.dart`'s `_handleList` route, which
  resolves `path` relative to `root` server-side (`path=''` → `.`, the root itself). The
  subdirectory-tile inline listing (`explorer_tree_tile.dart`) had the same bug in a different
  guise: a real error from `listDirectory` (e.g. `isAccessDenied`) still rendered as "Empty" since
  it only ever checked `entries.isEmpty`, never `.error` — now shows an inline "Access required" /
  "Grant" row (reusing the same grant flow, extracted to `explorer_access.dart` since hostd grants
  a workspace root, never an arbitrary subfolder — the fix for either is identical). Covered by
  `aura_client/test/desktop/explorer_service_test.dart`.
- §2 Explorer context menu: only the read-only half landed — copy path, copy relative path,
  refresh, and reveal-in-filesystem (Windows-only) are real. Rename, delete, new file, new folder,
  and open-in-terminal were still entirely absent. **Fixed in a later session (same day)**: all
  five now land as `dart:io`-direct operations via `WorkspaceFileService` (new `rename`/`delete`/
  `createFile`/`createDirectory` methods) rather than through hostd — per this repo's own
  CLAUDE.md, human writes go through `dart:io` against the workspace root, not through a hostd
  grant, the same way the existing editor save path already does; no new `HostdControlClient`
  method was needed after all. Rename/delete/new-file/new-folder dialogs and the destructive-delete
  confirmation live in a new `explorer_mutations.dart`, shared by `_FileTile` and `_DirectoryTile`;
  each mutation also publishes the corresponding `FileRenamed`/`FileDeleted`/`FileCreated` event,
  wiring up what were previously dead entries in `ide_event.dart` (defined, never published).
  "Open in terminal" turned out to need its own small fix first: `TerminalView` only ever renders
  the *first* terminal session for a workspace (a session tab strip is a tracked, disclosed D4
  follow-up, not built), so spawning a second session at the clicked directory would have been
  invisible. It instead starts the first session there directly if none exists yet, or sends a
  `cd` into the existing one, then reveals the bottom panel and switches its tab to Terminal —
  which required actually implementing `FocusTerminalRequested`'s handling in `bottom_panel.dart`,
  since that event was itself already being published by the Ctrl+`/Cmd+` shortcut with nothing
  ever subscribed to it. Covered by `aura_client/test/desktop/workspace_file_service_test.dart`.
- §4 Git fetch/pull/push: real and solid — full client→hostd→`git_service.dart`→real-`git` wiring
  for all three, a real branch-listing endpoint that's actually rendered in a branch-switcher
  dropdown (unlike the audit's `loadLog` dead-wiring finding), safe argv-only shell-out maintained,
  and structured `no_upstream`/`diverged`/`local_changes_would_be_overwritten` failures surfaced to
  the UI rather than silently swallowed. One real gap found: `git_service.dart`'s `_run` never set
  `GIT_TERMINAL_PROMPT=0` or a timeout, so a private remote with no configured credential helper
  could leave `git` blocking on an interactive prompt this headless daemon has no way to answer, or
  a slow/dead remote could hang the panel's Fetch/Pull/Push button indefinitely — **fixed in this
  same session**: `_run`/the new `_runNetwork` now set `GIT_TERMINAL_PROMPT=0` on every git
  invocation and fetch/pull/push run through `Process.start` with a 30s timeout that actually kills
  the process (not just gives up waiting on it), covered by
  `aura_hostd/test/git_service_test.dart`'s new "network timeout" group and two new real two-repo
  fetch/pull/push success tests (the prior suite only covered the no-remote/no-upstream paths).

Read-only static audit of `aura_client/lib/desktop/` and `aura_hostd/`, run against the claims in
`docs/AURA_UPGRADE_ROADMAP.md` (Phase K) and ADR-011/013/014, as the mandatory Phase 0 step of the
"AURA Desktop IDE — Production & Professional UX Upgrade Plan." No files were edited to produce
this report and no code was run: **this environment has no Flutter/Dart toolchain** (`flutter`/
`dart` are not on `PATH`), and the roadmap already records that every GitHub Actions job on this
repo has died before reaching a runner since 2026-09-22 (GAP-043). So nothing here can be marked
`VERIFIED` on the strength of "the workflow file looks right" or "the code compiles" — every row
below is either a citation of code actually read, or explicitly flagged `UNTESTED`/`CI-BROKEN
(GAP-043)` where only a real run or a green CI job would settle it.

Status vocabulary used throughout, matching the plan's own Phase 0 spec:
`VERIFIED` / `PARTIALLY VERIFIED` / `NOT IMPLEMENTED` / `BROKEN` (bug cited) / `UNTESTED` (plausible
from source, needs a real run or CI to confirm).

Seven areas were audited in parallel by independent read-only passes over the full text of each
file listed in their scope (not just grep matches). This document reconciles their findings; where
two areas' file lists overlapped, the more specific claim is kept.

## Top-line summary

| Area | Overall shape | Single biggest issue |
| --- | --- | --- |
| Shell / State / Theme / Routing | Real shell, real routing guard, real theme extension usage | **Layout persistence is a named exit criterion that doesn't exist**: `desktopLayoutState` is migrated into Drift but `LayoutNotifier` never reads/writes it, and there is no drag-to-resize UI anywhere — panels are fixed-size, not resizable, contrary to the plan text. |
| Explorer / Workspace / Editor | Editor abstraction is genuinely clean; workspace persistence is real | Explorer's error path shows raw exception text with only a generic "Retry" — never the plan's own "Grant Access" remediation UI. Right-click context menu is an unimplemented `TODO`. `HostFileBrowserPage` was never migrated onto the shared `ExplorerService`, so file-browsing logic now exists twice. |
| Terminal / PTY | A **real** Win32 ConPTY implementation exists | It is Windows-only (hardcoded `powershell.exe`, no POSIX PTY anywhere in the repo) and has **never been exercised** — no toolchain here, `hostd-ci.yml` runs on `ubuntu-latest` only (where the win32 FFI calls would fail), and the one test that touches the terminal route stops before starting a session. "Real PTY" today means "real-on-Windows-if-it-actually-runs, unproven either way." |
| Git / Diff | Stage/commit/checkout/status are real, end-to-end, and safely shelled out (argv lists, no injection) | No fetch/pull/push and no branch listing in the human Git panel; commit history is fully wired server-to-client but has zero UI callers; conflict handling exists only for the separate agent-task-merge flow, not the human's own checkout/merge path. |
| AI / Agent / Diagnostics / Debug | Debug is the strongest surface in the whole audit — a real DAP client talking to a real spawned `debugpy` over a real proxied socket; diagnostics parsing is real | Roadmap text is stale here (undercounts D5–D9 as "not started" when they're substantially built). Agent panel is a real chat surface wired to the genuine Engineer loop, but doesn't yet expose the plan's distinct plan/step/changed-files/terminal-command surfaces — those only exist as inline chat chips. |
| Host security / pairing / containment | Containment, command policy, and approval-gating are real and independently re-checked per request — the strongest safety story in the codebase | **Windows junctions are an unproven escape class**: `containment.dart` relies solely on `File.resolveSymbolicLinksSync()`, the one escape test that exists is unconditionally skipped on Windows, and there is no junction-specific test anywhere. Given the client targets Windows first, this is the biggest unverified security gap. |
| Packaging / CI / cross-platform | Expected pre-production state, now confirmed rather than assumed | No installer (no MSIX/Inno/DMG/AppImage), no code signing, no update/rollback mechanism, `aura_hostd` is not bundled with the client (user must clone/compile/configure it manually), no macOS CI job exists at all, and GAP-043 means Windows/Linux CI configs — however correctly written — have not actually run green recently. |

## 1. Shell / State / Theme / Routing

| Feature | Status | Evidence | Note |
| --- | --- | --- | --- |
| Persistent activity bar + collapse/expand | VERIFIED | `shell/desktop_shell.dart:87-146`, `shell/activity_bar.dart:41-116`, `state/state_notifiers.dart:604-631` | Toggle methods wired to activity bar taps and shortcuts. |
| Resizable sidebars / bottom panel | **BROKEN (not implemented)** | Setters exist (`state/state_notifiers.dart:592-602`) but `shell/desktop_shell.dart:96-143` only reads fixed widths from `layoutState`; no drag handle/`GestureDetector` anywhere under `desktop/`. | Panels only ever show default fixed sizes (280/320/200px, `state/desktop_application_state.dart:377-379`). The plan's "resizable" claim is unmet in code. |
| Desktop preference routing, no forced mobile redirect | VERIFIED | `core/routing/router.dart:256-263,293-392`; `core/platform/aura_platform.dart:14` | Redirect to `/ide` only fires when Windows + `SharedPreferences['desktop_use_ide']==true` + current location `/today`; mobile `ShellLayout` routes untouched. |
| Layout preserved across restart | **NOT IMPLEMENTED (table exists, unused)** | `core/database/database.dart:473` creates `desktopLayoutState`; `LayoutNotifier` (`state/state_notifiers.dart:570-638`) never references the DB at all. | Panel sizes/visibility reset to defaults every launch. Workspace list and editor tabs *do* persist correctly by contrast. |
| Keyboard shortcuts: command registry vs. ad hoc | PARTIALLY VERIFIED | `shell/desktop_shortcuts.dart:22-115` | Real Flutter `Actions`/`Shortcuts`/`Intent`, but a single flat map in one widget — not a discoverable/extensible command registry (no command palette, no external registration). |
| `IdeEvent` pub/sub wiring | PARTIALLY VERIFIED | Real publishers/consumers for workspace, tab, approval, git, diagnostics, and terminal events (see full list in agent transcript). | `FileChanged/Created/Deleted/Renamed`, `AgentStarted/AgentToolCalled/AgentCompleted`, `LayoutChanged`, `PanelVisibilityChanged` are defined but have **zero publish call sites** anywhere in `aura_client/lib` — dead events with waiting subscribers, consistent with being D6/D8 placeholders. |
| `AuraIdeDensity` real ThemeExtension, not scattered constants | VERIFIED (mostly) | Consumed via `context.ideDensity` across shell/activity bar/status bar/bottom panel/explorer/editor tab bar. | Where `AuraIdeDensity` is actually registered into `ThemeData.extensions` was out of this pass's file scope — **UNTESTED**; if it's never registered, every widget silently falls back to `defaultDensity` and the mechanism is inert even though consumption code is correct. |
| Spot-checked "fixed in this pass" bugs (roadmap-claimed) | 3 of 4 VERIFIED FIXED, 1 fixed with a residual smell | `DesktopShell` no longer boxes its whole body (`desktop_shell.dart:83-156`); sealed `IdeEvent` correctly single-library (`events/ide_event.dart:10-245`); `WorkspaceNotifier` no longer self-publishes (`state_notifiers.dart:34-38,62-68`); `HostNotifier.refreshStatus`'s dead `status['reachable']`-style map access still exists but is now unreferenced dead code (`state_notifiers.dart:675-692` vs. the wired `updateFromSnapshot` at `:883-895`) — side-stepped rather than removed. | |

## 2. Explorer / Workspace / Editor

| Feature | Status | Evidence | Note |
| --- | --- | --- | --- |
| hostd-first read with `dart:io` fallback | VERIFIED | `explorer/explorer_service.dart:82-120` | Real fallback on any hostd exception, not a stub. |
| "Host offline" banner | PARTIALLY VERIFIED | `explorer/explorer_panel.dart:58-76` | A status pill, not the plan's explicit read-only banner wording. |
| Readable access-denied UI ("Grant Access") | **NOT IMPLEMENTED** | `explorer/explorer_panel.dart:228-269` | `_ErrorView` shows raw `e.toString()` with a generic "Retry" only; no "Grant Access" affordance anywhere in the tree. |
| Explorer context menu (rename/delete/new/reveal/terminal/copy path/refresh) | **NOT IMPLEMENTED** | `explorer/explorer_tree_tile.dart:245-247` — `_showContextMenu()` body is a bare `// TODO` | Right-click does nothing; only the toolbar "Refresh" action exists. |
| `HostFileBrowserPage` reuse of `ExplorerService` | **BROKEN (duplication, not reuse)** | `host_file_browser_page.dart:48,67` calls `HostdControlClient` directly instead of delegating, contradicting the migration the roadmap claims (roadmap L926). | Two file-browsing code paths will now drift independently. |
| Recent workspaces persistence | VERIFIED | `workspace/desktop_workspace_repository.dart:15-50` — real Drift table `desktopWorkspaces`, not an in-memory list. | |
| Multi-tab editing, dirty indicators, pinning, close-with-confirmation | VERIFIED | `editor/editor_tab_bar.dart:36-46,58-97,171-192` | Save/Discard/Cancel dialog on close, save path goes through `WorkspaceFileService`. |
| Preview tabs | **NOT IMPLEMENTED** | No such concept anywhere in `editor_tab_bar.dart`. | |
| `EditorProvider` abstraction — real seam? | VERIFIED | `editor/editor_tab.dart` imports only the abstraction, never `flutter_code_editor` directly; `editor/code_editor_view.dart` goes through `ref.read(editorProviderProvider)`. | Concrete impl correctly isolated to `editor/providers/flutter_code_editor_provider.dart`. |
| Session restore: open tabs | VERIFIED | `state/state_notifiers.dart:259-277` (save), `:118-149` (load) — real Drift round-trip via `desktopEditorState`. | Confirms the roadmap's claim this was a stub and is now implemented. |
| Session restore: cursor/selection position | **NOT IMPLEMENTED** | `state/state_notifiers.dart:262-269` only serializes `id/path/title/isPinned`; `cursorPosition`/`selection` fields exist on `EditorTab` but are dropped on save. | Cursor position does not survive restart, despite "session restore" wording. |
| Quick open | PARTIALLY VERIFIED | `editor/quick_open_dialog.dart:40-73` walks the real workspace tree (`workspace_file_service.dart:62-113`, capped 5000 files, ignores `.git`/`node_modules`). | Matching is plain substring `contains()`, not fuzzy; `_highlightMatch` is a literal no-op stub. |

## 3. Terminal / PTY

| Feature | Status | Evidence | Note |
| --- | --- | --- | --- |
| Real PTY vs. `Process.start` pipe | **VERIFIED, Windows-only** | `aura_hostd/lib/src/pty_session.dart:1-338` — genuine Win32 ConPTY (`CreatePipe`→`CreatePseudoConsole`→`STARTUPINFOEX`+`UpdateProcThreadAttribute(PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE)`→`CreateProcess`, output read in a dedicated Isolate). | This is a real pseudoconsole, not a pipe. But it's the *only* PTY implementation — no `posix_openpt`/`forkpty`/`/dev/ptmx` anywhere in the repo, so there is no POSIX PTY for Linux/macOS. `win32`/`ffi` are unconditional pubspec deps, not platform-guarded. |
| OS-specific shell selection | **NOT IMPLEMENTED** | `pty_session.dart:25` hardcodes `shellExecutable = 'powershell.exe'`; `terminal_session_manager.dart:88-93` never overrides it. | No bash/zsh branch for Linux/macOS at all. Note: this is a *different* code path from the `run_command` agent tool's separate `sh -c` gap (ADR-011). |
| Resize handling | VERIFIED | Client `xterm.Terminal.onResize` → `resize()` RPC → `ResizePseudoConsole` (`pty_session.dart:293-303`). | Real end-to-end resize, not fixed dimensions. |
| stdin/stdout/stderr streaming | VERIFIED | Base64 `output`/`input` frames over the daemon RPC, rendered via a real `xterm.Terminal` client-side. | |
| Exit code propagation | VERIFIED | `GetExitCodeProcess` polled every 300ms → `exited` frame with `exit_code`. | |
| Working directory / environment | PARTIALLY VERIFIED | cwd is containment-checked and passed to `CreateProcess`; no custom environment variables are passed — child inherits the daemon's own env. | Not called out as a gap anywhere; unremarkable but real. |
| Process lifecycle | PARTIALLY VERIFIED | `TerminateProcess` on close/teardown; restart is a manual client button only. | No auto-restart-on-crash — matches the roadmap's own disclosed `HostdSupervisor` cut. |
| Interactive programs (vim, `git commit`, `top`) | **UNTESTED** | Architecturally plausible given a real ConPTY, but `hostd-ci.yml` runs on `ubuntu-latest` only (the win32 FFI calls would fail there), the one terminal-route test stops before starting a session, and the only manual exercise script (`bin/pty_smoke_test.dart`) has no evidence anyone has run it. | |

**Correction to the roadmap:** the scorecard line "Desktop Engineer (`aura_hostd`)... real PTY / live xterm ... remain open" (`docs/AURA_UPGRADE_ROADMAP.md` scorecard) is now **stale** — it describes Phase J's older non-PTY `run_command` stopgap, not Phase K/D4's ConPTY work, which post-dates that line and which Phase K's own status paragraph never separately addresses (it only speaks to D1–D3). The corrected claim is: a real PTY exists in source for Windows, but is unexercised by any toolchain or CI in this repo today.

## 4. Git / Diff

This area is materially ahead of the roadmap's own phase map (D5, listed after the still-unproven
D1–D3) — real, wired code exists here, deliberately kept separate from the agent's own git tools
(`aura_core/tools/coding.py`) so the human Git panel and the agent's git actions never share a code
path.

| Feature | Status | Evidence | Note |
| --- | --- | --- | --- |
| Branch/status/changed files | VERIFIED, real git | `aura_hostd/lib/src/git_service.dart:65-101` (`git status --short --branch`), tested against real `git init` repos (`git_service_test.dart:36-101`). | |
| Stage/unstage/commit | VERIFIED end-to-end | Client `git_notifier.dart:90-105` → `hostd_control_client_io.dart:172-179` → `control_server.dart:164-168,497-498,523` → `git_service.dart:158-182`. | Commit deliberately never runs `git add -A` first — commits exactly what's staged. |
| Branch create/checkout | PARTIALLY VERIFIED | `git_service.dart:184-198`, tested. | No branch **list** endpoint anywhere — the panel can't show or pick from existing branches. |
| Fetch/pull/push | **NOT IMPLEMENTED** | Full route list is `control_server.dart:158-184` — no `/git/fetch`, `/git/pull`, `/git/push`. | Git panel is local-only; no remote sync surfaced to the human. |
| History / file history | PARTIALLY VERIFIED, dead in UI | Server+client wired (`git_service.dart:134-156`, `git_notifier.dart:73-88`) but `loadLog` has **zero UI callers** anywhere in `aura_client/lib`. | Should be treated as NOT IMPLEMENTED from a user's perspective. |
| Merge conflict handling | PARTIALLY VERIFIED, wrong surface | Conflict detection exists only for the agent-task-merge flow in Control Center (`git_service.dart:263-292`, `control_center_notifier.dart:77-106`). | A manual checkout/merge in the human's own Git panel has no conflict path at all. |
| Diff viewer | PARTIALLY VERIFIED | `git_diff_page.dart` — unified-only, no side-by-side, no changed-file navigation, no hunk-level accept/revert. | |
| Shell-out safety | **VERIFIED SAFE** | `git_service.dart:360-367` — `Process.run('git', args, ...)`, pure argv list, no string interpolation into a shell command anywhere in the file; file args always passed after `--`. | No shell-injection-shaped code found — the strongest-built part of this subsystem. |
| Test coverage | VERIFIED, real-git | `aura_hostd/test/git_service_test.dart` uses real temp dirs + real `git init`, never mocks `Process.run`. | No test exercises `mergeTask`'s conflict path or `diffStat`. |

## 5. AI / Agent / Diagnostics / Debug

The roadmap's phase map (`docs/AURA_UPGRADE_ROADMAP.md:855-933`) places D6–D9 as work scheduled
*after* D1–D3, and doesn't mark them done — but the actual code already contains substantial,
cross-referenced D5–D9 implementations (doc comments cite "D6 plan section C.3," "D8 plan Part B,"
etc.). **Treat the table below, not the roadmap phase map, as current ground truth**; the roadmap
undercounts what exists here.

| Feature | Status | Evidence | Note |
| --- | --- | --- | --- |
| Agent panel UI richness | PARTIALLY VERIFIED | `agent/agent_panel.dart:99-241` — chat-style, task switcher, `PendingActionsCard`, `ToolCallChips`. | No dedicated plan/step/changed-files/terminal-command surfaces — those exist only as inline chat artifacts, not the plan's mockup's separate panels. |
| Agent panel wired to the real Engineer loop | VERIFIED | Uses `chatViewModelProvider`/`chatRepositoryProvider` forced to the Engineer agent — same pipeline as mobile Copilot → backend v3 chat → `AIKernel`/`ToolRegistry` → sandbox → hostd (ADR-011). Not mocked. | |
| Diagnostics parser | VERIFIED | `diagnostics/diagnostics_parser.dart:15-196` — real per-tool parsers (dart analyze machine format, ruff JSON, pyflakes regex, eslint JSON) into structured `Diagnostic` entries. | |
| Diagnostics lifecycle + Problems panel navigation | VERIFIED | Scan-on-open/save(debounced)/manual rerun; click opens the tab and jumps to line:col. | |
| `dap_client.dart` | VERIFIED, real DAP wire protocol | `Content-Length:`-framed JSON-RPC requests with seq correlation, real response/event parsing. | |
| `debug_session_manager.dart` (hostd) | VERIFIED, real | Spawns `python -m debugpy --listen ... --wait-for-client`, connects a real TCP socket, proxies raw bytes bidirectionally as base64 `dap` frames over the daemon WS. | |
| `debug_notifier.dart` | VERIFIED, real | Runs the actual `initialize→attach→setBreakpoints→configurationDone` handshake and real `stackTrace`/`scopes`/`variables`/step DAP requests. | |
| "No fake debugging UI" plan warning | **HONORED** | Every debug control calls into the real DAP request path above; nothing is decorative. | Python/`debugpy` only; breakpoints set via a line-number field rather than gutter click — a disclosed limitation, not a fake one. |
| Control Center | PARTIALLY VERIFIED, real but narrow | Lists/diffs/merges/discards real per-task git worktrees via real hostd RPCs. | Not live orchestration — the daemon has no running/in-progress status field, only "worktree currently exists or not" (disclosed in-code). |

## 6. Host security / pairing / containment

The strongest safety story in the codebase: containment, command policy, and approval-gating are
real, tested against actual escape attempts (not just happy paths), and independently re-checked on
every request rather than cached from grant time.

| Feature | Status | Evidence | Note |
| --- | --- | --- | --- |
| Containment (path traversal / symlink escape) | VERIFIED | `aura_hostd/lib/src/containment.dart:107-124,191-205`; tests actually create a symlink pointing outside the root and assert `ContainmentViolation` (`containment_test.dart:93-109`). | Real negative-path tests, plus a root denylist (`/`, `/etc`, home dir) at grant time. |
| Command policy | VERIFIED | `command_policy.dart:37-47` hard denylist (rm -rf variants, fork bomb, mkfs, dd of=/dev/, curl\|sh); `:58` blocks chaining/pipe/redirect/substitution metacharacters before the allowlist check; `:64-78` deliberately excludes `find`/`env` to prevent laundering. | Documented itself as "a seatbelt, not a cage" — no process-level sandboxing; anything past the gate runs as the user's own OS account. |
| Pairing | PARTIALLY VERIFIED | `pairing_client.dart:81-131` — a real OAuth device-flow-style handshake against the backend, not a `==` shared-token compare. | Daemon-side is entirely trust-the-backend; no local attestation/signature. |
| Approval queue actually gates execution | VERIFIED | `task_handler.dart:262-264,108-111,189-192` — every mutating/executing RPC awaits `_awaitApproval`/`_awaitWriteApproval` first, and re-derives the operation hash post-approval to refuse on mismatch. | No bypass path found. |
| Workspace grants re-checked per request | VERIFIED | Every handler calls `requireContained`/`findContainingRoot` fresh each time; nothing caches a grant-time decision. | Matches containment.dart's own stated design. |
| Client-side `host_panel.dart` making its own security decisions | **NOT IMPLEMENTED (invariant holds)** | Pure display/status UI; the actual RPC client does no pre-filtering. | "hostd is authoritative, client is UX validation" holds for this surface. |
| Pairing auth vs. `DeviceValidationService` | PARTIALLY VERIFIED / divergent, by design | Local hostd IPC authenticates with a loopback bearer token from `control.json`, a separate and explicitly weaker mechanism than the backend's attestation headers, documented in-code as "a convenience boundary... not a defense against a compromised machine." | Different trust boundary (loopback IPC vs. backend API), not a violation of the client-attestation rule, but worth tracking. |
| **Windows junctions / reparse points** | **NOT IMPLEMENTED / UNTESTED — security-critical gap** | `containment.dart` has zero mentions of junctions/reparse points and relies solely on `File.resolveSymbolicLinksSync()`; the one symlink-escape test is unconditionally **skipped on Windows** with no junction-based substitute anywhere in `test/`. | Windows directory junctions (`mklink /J`) don't require admin privileges (unlike Windows symlinks), and this exact escape class is called out in the production plan's own security-validation phase. This is the single biggest unverified gap in the whole audit given the client targets Windows first. |

## 7. Packaging / CI / cross-platform

Every conclusion here is qualified by GAP-043 (`docs/AURA_UPGRADE_ROADMAP.md`): no GitHub Actions
job on this repo has reached a runner since 2026-09-22, so "the workflow file is written correctly"
is at most `PARTIALLY VERIFIED`, never `VERIFIED`, until a green run is observed.

| Feature | Status | Evidence | Note |
| --- | --- | --- | --- |
| Windows build/CI | PARTIALLY VERIFIED (CI-BROKEN, GAP-043) | `.github/workflows/client-ci.yml:87-125` — real `build-windows` job, `continue-on-error: true` by design. | Non-gating by design; no confirmed recent green run either way. |
| Linux build/CI | PARTIALLY VERIFIED (CI-BROKEN, GAP-043) | `client-ci.yml:127-171` — real, gating `build-linux` job. | Would gate if it ran; same GAP-043 blackout. |
| macOS build/CI | **NOT IMPLEMENTED** | Zero `macos`/`darwin` matches in `client-ci.yml`; confirmed deliberate in ADR-011 and the roadmap ("add one if and when macOS/Windows desktop support is actually claimed"). | `scripts/deploy_client_desktop.sh` will run `flutter build macos` on a Darwin host, but nothing in CI ever validates that path — macOS support is purely theoretical. |
| `hostd-ci.yml` | PARTIALLY VERIFIED (CI-BROKEN, GAP-043) | Real, gating `dart analyze --fatal-infos` + `dart test`. | Same blackout; "last-known-good" cannot currently be claimed. |
| Installer/packaging | **NOT IMPLEMENTED** | Zero matches for msix/inno/distributor/notariz/dmg/appimage across `aura_client/` and `scripts/`; `client-ci.yml` itself notes "no signing config wired in CI, no packaging step for Linux." | Only raw, unsigned `flutter build <platform> --release` bundles. |
| Bundled `aura_hostd` | **NOT IMPLEMENTED (confirmed)** | `scripts/deploy_client_desktop.sh`/`.ps1` state explicitly that hostd is not bundled and must be built/configured separately; no reference in `pubspec.yaml`. | User must manually clone, compile, and point the client at the daemon binary before the desktop Engineer feature works at all. |
| Update/rollback mechanism | **NOT IMPLEMENTED** | No update-check/auto-updater code anywhere in the client or scripts. | |
| `deploy_client_desktop.*` / `run_desktop_stack.*` | PARTIALLY VERIFIED — dev convenience, not real deployment | Wrap `flutter build`/`dart compile exe` and launch both binaries locally; `run_desktop_stack`'s own header states it does not pair the daemon or grant a workspace root. | Not an installer or production deploy path. |

## Net read for Priority ordering (plan §29)

This audit does not contradict the plan's stated priority order (P0 audit → P1 design system → …
→ P6 PTY terminal → …), but it changes what several of those phases actually have to do, based on
what's already real vs. only claimed:

- **P1 (design system) / P2 (shell)**: the layout-persistence and resize gaps found here (§1) are
  concrete, scoped bugs to fix as part of this phase, not new scaffolding — the Drift table and the
  panel-size state already exist; they're just not wired together, and no resize gesture exists yet.
- **P3 (Explorer)**: the plan's own "Grant Access" error-UX example (§7 of the plan) is not a
  hypothetical mockup — it's the literal fix needed for `explorer_panel.dart`'s current raw-exception
  `_ErrorView`. The `HostFileBrowserPage`/`ExplorerService` duplication should be collapsed in the
  same pass to avoid the two paths drifting further.
- **P6 (PTY terminal)**: reframe from "build a PTY" to "prove and extend the PTY that already
  exists." The Win32 ConPTY implementation is real; the work is (a) getting it actually exercised —
  by CI on a Windows runner, or a manual smoke pass — and (b) adding the POSIX side (`posix_openpt`/
  `forkpty`) and non-hardcoded shell selection for Linux/macOS, which currently doesn't exist at all.
- **P7 (Git/Diff)**: this phase is further along than its position in the priority order suggests.
  The remaining work is scoped and concrete: branch listing, fetch/pull/push, wiring the already-built
  history feature into the UI, and giving the human Git panel its own conflict-handling path (today
  conflict handling exists only for the agent's worktree-merge flow).
- **P9 (Problems/Tests/Debug)**: Debug is materially further along than the roadmap credits — real
  DAP, real `debugpy`. This phase's real remaining work is breadth (more languages/adapters than
  Python) and UX (gutter breakpoints instead of a line-number field), not building a debugger from
  scratch.
- **P11 (Security/Approvals)** and **P17 (Security certification)**: the Windows-junction gap in
  §6 should be treated as a launch blocker for any Windows GA claim, not deferred to the later
  adversarial-validation phase — it's already precisely specified and testable today (add a
  junction-based `containment_test.dart` case analogous to the existing symlink one, on a Windows
  runner).
- **P15 (Packaging/Updates)**: nothing here was ambiguous — no installer, no bundled hostd, no
  updater, no macOS CI. This phase starts from zero, as the roadmap already implied but had not
  confirmed file-by-file until this pass.

## Methodology note

Each of the seven areas above was audited by an independent pass that read the full text of every
file in its scope (not grep excerpts), cross-referenced against `docs/AURA_UPGRADE_ROADMAP.md` and
the relevant ADR(s), and was explicitly instructed to cite `file:line` for every claim and to mark
anything requiring a real run or CI as `UNTESTED` rather than inferring success from code shape
alone. No code was modified by this audit.
