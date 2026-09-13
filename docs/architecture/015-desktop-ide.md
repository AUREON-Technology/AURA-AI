# ADR-015 — AURA Desktop IDE: Windows-first IDE shell with AURA-native workspace interaction

**Status:** Proposed (D0 architecture phase) · **Date:** 2026-09-13

## Context

The Engineer persona (`aura_core/agents/registry.py`) and its tools (`aura_core/tools/coding.py`) already execute on the user's machine via `aura_hostd` (ADR-011). The server-side persistent workspace model (ADR-013/014) provides durable per-(user, repo) Docker workspaces. The Flutter client (`aura_client`) currently has a mobile-first `ShellLayout` (Today / Copilot / Life Hub / You) and an engineer-daemon supervision page.

What is missing: a **human-facing IDE** that becomes the control surface for the same workspace model the agent operates on. Not a port of the mobile app — an IDE where AURA is a first-class workspace participant, not a sidebar chat.

## Decision

### 1. Three-surface UX target

```
             AURA DESKTOP
                  │
     ┌────────────┼────────────┐
     ↓            ↓            ↓
 WORKSPACE    INTELLIGENCE   CONTROL
     │            │            │
 Editor         AURA        Approvals
 Files          Agents      Host
 Git            Plans       Processes
 Terminal       Context     Tasks
 Tests          Memory      Security
```

D0–D3 establishes **Workspace** (editor, files, layout) with architectural plumbing for Intelligence and Control to integrate naturally in D4+.

### 2. Dual-shell architecture, not responsive scaling

```
Mobile (existing)          Desktop (new /ide/*)
┌─────────────────┐        ┌─────────────────┐
│ ShellLayout     │        │ DesktopShell    │
│ /today          │        │ /ide/workspace  │
│ /chat           │        │ /ide/explorer   │
│ /life           │        │ /ide/editor     │
│ /you            │        │ /ide/agent      │
└─────────────────┘        └─────────────────┘
         │                        │
         └────────────┬───────────┘
                      ↓
            Shared Core (hostd, sync, design_system, auth, DB)
```

Platform gate: `AuraPlatform.isDesktopIde => !kIsWeb && defaultTargetPlatform == TargetPlatform.windows`

**No forced redirect.** Authenticated Windows user chooses via Settings → Desktop experience:
- ○ AURA Home (`/today`)
- ● AURA IDE (`/ide`)

"Open Desktop IDE" command in palette. Default can flip when IDE matures.

### 3. Filesystem split (security)

| Actor | Operation | Mechanism |
|-------|-----------|-----------|
| Human (IDE) | Read | `HostdControlClient.listDirectory/readFile` (granted roots) |
| Human (IDE) | Write | `dart:io` `File` (paths under active workspace root) |
| Agent | Read/Write | Backend → hostd WebSocket (`TaskHandler`) |

**Critical rule:** Client containment = UX safety / accidental access prevention. Host/backend containment = actual security boundary. Client **never** treats its own check as the authority.

```
Client containment (UX validation)
        ↓
Host/backend containment (authoritative)
        ↓
filesystem
```

### 4. Shared containment utilities

Extract common test vectors to prevent client/host drift:
```
workspace/src/a.dart          ALLOW
workspace/../secret           DENY
workspace/link → /etc         DENY
workspace/a/../../secret      DENY
```
Located in `aura_client/lib/core/security/path_containment.dart` (client UX validation). Hostd (`aura_hostd/lib/src/containment.dart`) remains authoritative. Both share test vectors.

### 5. Editor abstraction (prevent lock-in)

```dart
abstract class EditorProvider {
  EditorController createController(EditorDocument doc);
  LanguageMode languageModeForPath(String path);
}

abstract class EditorDocument { /* content, version, path */ }
abstract class EditorSelection { /* start, end */ }
abstract class EditorCursor { /* position */ }
abstract class EditorCommand { /* save, undo, format, gotoDefinition, etc. */ }
```

`flutter_code_editor` + `highlight` is the **initial provider** (D3). Abstraction enables future swap (LSP, Monaco, custom) without IDE rewrite.

### 6. Drift v12 in D1 (local-only tables)

Schema version 12 adds **local-only** tables (not synced via `/api/v3/sync`):
- `DesktopWorkspaces` — `id`, `rootPath`, `displayName`, `lastOpenedAt`
- `DesktopEditorState` — `workspaceId`, `openTabsJson`, `activeTabPath`, `sidebarWidth`, `agentPanelWidth`, `bottomPanelHeight`
- `DesktopTerminalState` — placeholder for D4
- `DesktopLayoutState` — window geometry, panel visibility
- `DesktopAgentState` — placeholder for D6

### 7. `AuraIdeDensity` as `ThemeExtension`

```dart
@immutable
class AuraIdeDensity extends ThemeExtension<AuraIdeDensity> {
  final double activityBarWidth;
  final double primarySidebarWidth;
  final double secondarySidebarWidth;
  final double titleBarHeight;
  final double tabHeight;
  final double statusBarHeight;
  final double bottomPanelHeight;
  final EdgeInsets editorPadding;
  final double treeRowHeight;
  final double compactControlHeight;
  final double spacingScale; // multiplier for AuraSpacing
  // copyWith, lerp
}
```
Applied only under `DesktopShell` via `Theme(data: theme.copyWith(extensions: {AuraIdeDensity(...)}), child: DesktopShell)`.

### 8. Desktop State Architecture (Ownership Matrix)

| State Domain | Owner | Examples |
|--------------|-------|----------|
| **Server-owned** | Backend | Agent task, execution, memory, projects, goals, approvals |
| **Desktop-local** | Client | Open tabs, editor layout, window layout, recent files, terminal UI, selected workspace |
| **Host-owned** | Hostd | Host connection, granted roots, PTY sessions, local processes, workspace filesystem |

Riverpod notifiers per domain (no giant monolithic state):
```
DesktopApplicationState
├── WorkspaceState (DesktopWorkspaces + active)
├── EditorState (tabs, active, dirty, persistence)
├── TerminalState (D4)
├── GitState (D5)
├── AgentState (D6)
├── TaskState (D8)
├── HostState (connection, roots)
├── LayoutState (panel sizes, visibility)
├── ApprovalState (pending confirmations)
└── NotificationState (toasts, badges)
```

### 9. IDE Event Model

Unified event stream (not polling):
```dart
sealed class IdeEvent { const IdeEvent(); }

class WorkspaceOpened extends IdeEvent { final String workspaceId; }
class FileChanged extends IdeEvent { final String path; final String workspaceId; }
class FileSaved extends IdeEvent { final String path; }
class TerminalStarted extends IdeEvent { final String sessionId; }
class TerminalOutput extends IdeEvent { final String sessionId; final String data; }
class AgentStarted extends IdeEvent { final String taskId; }
class AgentToolCalled extends IdeEvent { final String tool; final Map args; }
class ApprovalRequested extends IdeEvent { final String approvalId; final String tool; }
class GitChanged extends IdeEvent { final String workspaceId; }
class DiagnosticAdded extends IdeEvent { final String path; final List<Diagnostic> items; }
class TaskUpdated extends IdeEvent { final String taskId; }
```
Enables: agent changes file → editor refreshes → Git updates → Problems update → AI panel updates.

### 10. Phase map

```
D0 — Desktop Product Architecture (this ADR + containment test vectors)
     ↓
D1 — Desktop Shell + State + Theme + Abstractions
     ↓
D2 — Workspace + Filesystem (Hostd integration)
     ↓
D3 — Professional Editor (abstraction + persistence)
     ↓
D4 — Terminal + PTY
     ↓
D5 — Git + Diff
     ↓
D6 — AURA AI Coding Layer (inline, diff, multi-file)
     ↓
D7 — Tests + Problems + Debug
     ↓
D8 — Agent Workspace (persistent per-repo)
     ↓
D9 — Multi-Agent Control Center
     ↓
D10 — Personal AI OS Desktop
```

### 11. Migration / reuse map

| Existing | Use in IDE |
|----------|------------|
| `HostdControlClient` | Explorer read + grant roots |
| `HostDaemonPage` | Full settings; slim `HostPanel` in IDE sidebar footer |
| `HostFileBrowserPage` | Delegate listing to shared `ExplorerService` |
| `SandboxFileView` / diff widgets | **Not moved yet** — D6 diff review |
| `ChatPage` | Agent panel placeholder → embed or route `/chat` in secondary sidebar in D6 |
| `engineer_run_repository` | Task center D8 |
| `AuraPalette` / `AuraTypography` | Extend, don't replace |

## Consequences

- New `lib/desktop/` directory tree (greenfield, no mobile impact)
- New dependencies: `flutter_code_editor`, `highlight`, `freezed` + `freezed_annotation`, `json_annotation`
- Windows CI job added (non-blocking for D1–D3 merge)
- Mobile/Android unchanged — existing `ShellLayout` and navigation intact
- Editor abstraction pays forward: LSP, Monaco, or custom provider swap is one implementation, not a rewrite
- State ownership matrix prevents desktop client from becoming a giant state-management mess
- Event model enables reactive UI without polling

## Implementation status

- **D0:** This ADR + `docs/architecture/015-desktop-ide.md` + containment test vectors (proposed)
- **D1–D3:** Not started

## Related

- [ADR-011](011-desktop-engineer-agent.md) — daemon executes agent tools on user's machine
- [ADR-013](013-agent-workspaces.md) — server-side persistent Docker workspaces
- [ADR-014](014-workspace-local-side-effects.md) — confirmation at workspace boundary
- [AURA_UPGRADE_ROADMAP.md](../AURA_UPGRADE_ROADMAP.md) — Phase K (Desktop IDE)