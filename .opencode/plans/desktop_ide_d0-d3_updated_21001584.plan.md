---
name: Desktop IDE D0–D3 (Updated)
overview: Introduce Windows-first AURA Desktop IDE with architectural foundations (D0), shell + state + theme (D1), workspace manager + hostd-backed explorer (D2), and multi-tab editor with abstraction layer (D3). No forced redirect; explicit desktop preference; AI-native workspace interaction as defining characteristic.
todos:
  - id: d0-architecture-foundations
    content: "D0: Desktop architecture doc (state ownership, event model, workspace model, security boundaries, editor abstraction, hostd boundary, persistence boundaries, desktop/mobile separation); ADR-015; AURA_UPGRADE_ROADMAP Phase D"
    status: pending
  - id: d0-containment-utils
    content: "D0: Shared path containment utilities + test vectors (client UX validation, hostd authoritative); aura_common or shared package"
    status: pending
  - id: d1-platform-router-shell
    content: "D1: AuraPlatform gate; DesktopShellRoute (/ide) with preference gating (not forced redirect); DesktopShell layout (activity bar, sidebars, bottom, status); command palette IDE entries; Windows CI job"
    status: pending
  - id: d1-design-system
    content: "D1: AuraIdeDensity ThemeExtension (activityBarWidth, sidebarWidths, tabHeight, statusBarHeight, editorPadding, treeRowHeight, compactControlHeight, spacingScale); editor color tokens"
    status: pending
  - id: d1-drift-v12
    content: "D1: Drift schema v12 — DesktopWorkspaces, DesktopEditorState, DesktopTerminalState, DesktopLayoutState, DesktopAgentState tables; migration; build_runner"
    status: pending
  - id: d1-editor-abstraction
    content: "D1: IdeEditor abstraction (EditorDocument, EditorSelection, EditorCursor, EditorCommand, EditorProvider); flutter_code_editor + highlight as initial provider"
    status: pending
  - id: d1-state-architecture
    content: "D1: DesktopApplicationState + notifiers (WorkspaceState, EditorState, TerminalState, LayoutState, ApprovalState, HostState); Riverpod-owned; server/host/desktop ownership matrix documented"
    status: pending
  - id: d1-event-model
    content: "D1: IDE event model (workspace.opened, file.changed, file.saved, terminal.*, agent.*, approval.requested, git.changed, diagnostic.added, task.updated); event bus / stream"
    status: pending
  - id: d2-workspace-manager
    content: "D2: Workspace repository/notifier (CRUD, recent, active); welcome page; workspace manager UI; open-folder + grant root flow (hostd grantRoot CTA); hostd fallback to dart:io read-only"
    status: pending
  - id: d2-explorer
    content: "D2: Explorer panel (hostd listDirectory + dart:io fallback); tree widget; reveal in OS, refresh, copy path; shared explorer service (refactor HostFileBrowserPage); HostPanel extraction"
    status: pending
  - id: d3-editor
    content: "D3: EditorTab immutable model (id, path, title, isDirty, isPinned, viewState, cursorPosition, selection, lastAccessed); tab bar; code_editor_view via EditorProvider; dirty/save; session persistence to Drift"
    status: pending
  - id: d3-file-io
    content: "D3: WorkspaceFileService (read/write with client-side containment check mirroring hostd rules); quick open (Ctrl+P fuzzy filter, ignore .git/node_modules/.dart_tool); shortcuts (Ctrl+S/W/P/`, Ctrl+B)"
    status: pending
  - id: d3-tests
    content: "Tests: containment test vectors (shared); workspace path containment; editor session notifier; shell/explorer smoke widgets; Windows manual verification checklist; CI Linux analyze + tests pass"
    status: pending
isProject: false
---

# AURA Desktop IDE — D0–D3 (Windows-first, Updated)

## Strategic Positioning

This plan builds the **foundation of an AURA-native IDE**, not merely a desktop port of the mobile app. The defining characteristic of AURA Desktop is:

> **"Work with me inside the repository."**

Not: "Chat with me about your code."

The existing AURA workspace architecture (per-repo persistent workspaces, isolation boundaries, hostd containment) already exists server-side. The Desktop IDE becomes the **human-facing control surface for that same workspace model**.

---

## Three-Surface UX Target

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

D0–D3 establishes the **Workspace** surface (editor, files, layout) with the architectural plumbing that lets Intelligence and Control integrate naturally in D4+.

---

## Phase Map

```
D0 — Desktop Product Architecture
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

---

## Current Baseline (Verified)

| Area | State |
|------|--------|
| Navigation | Single `ShellLayout` (Today / Copilot / Life Hub / You) via `NavigationRail` ≥768px |
| Engineer / hostd | `HostDaemonPage`, `HostFileBrowserPage` (read-only via `HostdControlClient`) |
| Agent UI | Chat-centric: `engineer_confirmation_card.dart`, `sandbox_terminal_view.dart` |
| `lib/desktop/` | **Does not exist** — greenfield |
| hostd control API | List/read + roots only; no user write path (agent writes via WebSocket `TaskHandler`) |
| CI desktop | Linux build gate in `.github/workflows/client-ci.yml`; Windows relies on local `deploy_client_desktop.ps1` |
| Design system | `AuraPalette`, `AuraTypography`, `AuraSpacing`, `AuraRadius` — no IDE density tokens |
| Drift schema | v11 (see `database.dart:279`) |
| Router | Single `ShellRoute` at `/today`, `/chat`, `/life`, `/you`, etc. |

---

## Architectural Decisions (This Pass)

### 1. Platform Gate, Not Width Gate
`aura_client/lib/core/platform/aura_platform.dart`:
```dart
bool get isDesktopIde => !kIsWeb && defaultTargetPlatform == TargetPlatform.windows;
```
Avoids treating "wide mobile layout" as desktop IDE.

### 2. Dual Shells, Shared Core
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

**No forced redirect.** Authenticated Windows user sees preference:
```
Settings → Desktop experience
  ○ AURA Home (/today)
  ● AURA IDE (/ide)
```
"Open Desktop IDE" command in palette. Default can flip later when IDE is mature.

### 3. Filesystem Split (Security)

| Actor | Path | Mechanism |
|-------|------|-----------|
| Human (IDE) | Read | `HostdControlClient.listDirectory/readFile` (granted roots) |
| Human (IDE) | Write | `dart:io` `File` (paths under active workspace root) |
| Agent | Read/Write | Backend → hostd WebSocket (`TaskHandler`) |

**Critical rule:** Client containment = UX safety / accidental access prevention. Host/backend containment = actual security boundary. Client **never** treats its own check as the authority.

```
Client containment (UX)
        ↓
Host/backend containment (authoritative)
        ↓
filesystem
```

### 4. Shared Containment Utilities
Extract common test vectors to prevent client/host drift:
```
workspace/src/a.dart          ALLOW
workspace/../secret           DENY
workspace/link → /etc         DENY
workspace/a/../../secret      DENY
```
Located in shared package (e.g., `aura_common` or `aura_client/lib/core/security/path_containment.dart` with mirrored logic). Hostd (`containment.dart`) remains authoritative.

### 5. Editor Abstraction (Prevent Lock-in)
```dart
// D1: Define abstraction
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

### 6. Drift v12 in D1 (Not D2)
Schema version 12 adds **local-only** tables (not synced):
- `DesktopWorkspaces` — `id`, `rootPath`, `displayName`, `lastOpenedAt`
- `DesktopEditorState` — `workspaceId`, `openTabsJson`, `activeTabPath`, `sidebarWidth`, `agentPanelWidth`, `bottomPanelHeight`
- `DesktopTerminalState` — placeholder for D4
- `DesktopLayoutState` — window geometry, panel visibility
- `DesktopAgentState` — placeholder for D6

Run `dart run build_runner build --delete-conflicting-outputs` after schema change.

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

**Riverpod notifiers per domain** (no giant monolithic state):
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
sealed class IdeEvent {
  const IdeEvent();
}

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

---

## D0 — Desktop Product Architecture (New)

**Deliverable:** Architecture document + ADR-015 + Phase D in `AURA_UPGRADE_ROADMAP.md` before any code.

| Component | Path | Notes |
|-----------|------|--------|
| Architecture doc | `docs/architecture/015-desktop-ide.md` | State ownership, event model, workspace model, security boundaries, editor abstraction, hostd boundary, persistence boundaries, desktop/mobile separation |
| ADR | `docs/architecture/015-desktop-ide.md` (same) | Records decisions above |
| Roadmap | `docs/AURA_UPGRADE_ROADMAP.md` | New **Phase D** section with D0–D10 map |
| Containment utilities | `lib/core/security/path_containment.dart` | Shared test vectors; client UX validation only |

**Exit criteria (D0):** Doc reviewed; ADR merged; Roadmap updated; containment test vectors defined and passing in both client and hostd test suites.

---

## D1 — Desktop Shell + State + Theme + Abstractions

**Deliverable:** Credible IDE skeleton with platform gate, routing, theming, state architecture, and editor abstraction — empty panels OK.

| Component | Path | Notes |
|-----------|------|--------|
| Platform gate | `core/platform/aura_platform.dart` | `isDesktopIde` |
| Desktop preference | `features/settings/presentation/desktop_preference_page.dart` | Radio: AURA Home / AURA IDE; persists to `SharedPreferences` or Drift |
| Router | `core/routing/router.dart` | New `ShellRoute` builder: `DesktopShell(child:)` under `/ide`; redirect respects preference |
| Shell | `desktop/shell/desktop_shell.dart` | `Row`: ActivityBar \| PrimarySidebar \| MainWorkspace \| SecondarySidebar (agent) |
| Activity bar | `desktop/shell/activity_bar.dart` | Icons: Explorer, Search (stub), SCM (stub), Agents (stub), AURA (stub), Settings |
| Main area | `desktop/shell/main_workspace.dart` | Hosts editor group + welcome view |
| Bottom panel | `desktop/shell/bottom_panel.dart` | Tab strip: Terminal / Output / Problems (placeholders) |
| Status bar | `desktop/shell/status_bar.dart` | Branch placeholder, hostd connection, backend URL snippet |
| Welcome | `desktop/workspace/workspace_welcome_page.dart` | Recent workspaces + Open Folder |
| Command palette | `features/command_palette/command_palette.dart` | Add IDE commands: Open Workspace, Explorer, Pair Host, Quick Open (stub) |
| Design tokens | `core/design_system/aura_ide_density.dart` | `AuraIdeDensity` ThemeExtension |
| Editor abstraction | `desktop/editor/editor_abstraction.dart` | `EditorProvider`, `EditorDocument`, `EditorSelection`, `EditorCursor`, `EditorCommand` |
| State notifiers | `desktop/state/` | `WorkspaceState`, `EditorState`, `LayoutState`, `HostState`, `ApprovalState` |
| Event bus | `desktop/events/ide_event_bus.dart` | Stream/IdeEvent; Riverpod provider |
| Docs | `docs/architecture/015-desktop-ide.md` | Updated with D1 decisions |
| CI | `.github/workflows/client-ci.yml` | Add Windows desktop build job (non-blocking for D1–D3 merge) |

**Exit criteria (D1):** Windows build shows persistent activity bar + sidebars + bottom + status; mobile/Android unchanged; desktop preference works; `flutter analyze` clean on Linux and Windows; Drift v12 migration runs; editor abstraction compiles.

---

## D2 — Workspace + Explorer

**Deliverable:** Manage real repo folders; navigate file tree; open files into editor (D3 consumes).

| Component | Path | Notes |
|-----------|------|--------|
| Workspace repository | `desktop/workspace/desktop_workspace_repository.dart` | CRUD on `DesktopWorkspaces` |
| Workspace notifier | `desktop/workspace/desktop_workspace_notifier.dart` | Active workspace id, hostd roots cache |
| Workspace manager UI | `desktop/workspace/workspace_manager_page.dart` | Recent list, open folder, remove recent |
| Explorer panel | `desktop/explorer/explorer_panel.dart` | Tree: hostd `listDirectory` when root granted; else `dart:io` directory listing |
| Tree widget | `desktop/explorer/explorer_tree_tile.dart` | Expand/collapse, icons by extension |
| Explorer actions | — | Reveal in OS (`url_launcher` / `Process.run` explorer), refresh, copy relative path |
| Host panel embed | `desktop/host/host_panel.dart` | Extract reusable sections from `HostDaemonPage` (status, pair, roots) — keep full page at `/engineer-daemon` |
| Shared explorer service | `desktop/explorer/explorer_service.dart` | Refactor `HostFileBrowserPage` to delegate listing |
| Welcome page wiring | `desktop/workspace/workspace_welcome_page.dart` | "Open Folder" → `FilePicker.getDirectoryPath()` → persist → optional "Grant to hostd" CTA |

**Explorer actions in D2:** reveal in OS, refresh, copy relative path. **Defer** create/rename/delete to D5.

**Exit criteria (D2):** Open `E:\AURA AI` (or any folder) → tree matches disk; granting root updates hostd; recent workspaces survive restart; hostd down → explorer still reads via `dart:io` (read-only banner: agent unavailable).

---

## D3 — Multi-Tab Editor (Professional)

**Deliverable:** Professional-enough editing for daily use; abstraction layer prevents lock-in.

| Component | Path | Notes |
|-----------|------|--------|
| EditorTab (immutable) | `desktop/editor/editor_tab.dart` | `id`, `path`, `title`, `isDirty`, `isPinned`, `viewState`, `cursorPosition`, `selection`, `lastAccessed` — use `freezed` if added; else manual `copyWith` |
| EditorState notifier | `desktop/editor/editor_session_notifier.dart` | Load/save, tab list, active tab, persist to `DesktopEditorState` |
| Tab bar | `desktop/editor/editor_tab_bar.dart` | Close, dirty dot, middle-click close, pin |
| Editor surface | `desktop/editor/code_editor_view.dart` | `EditorController` via `EditorProvider` + language mode from extension |
| File I/O | `desktop/workspace/workspace_file_service.dart` | Read/write with client-side containment check (Windows `dart:io`); mirrors hostd rules |
| Quick open | `desktop/editor/quick_open_dialog.dart` | Ctrl+P fuzzy filter on workspace file list (walk tree, ignore `.git`, `node_modules`, `.dart_tool`) |
| Keyboard shortcuts | `desktop/shell/desktop_shortcuts.dart` | Ctrl+S save, Ctrl+W close tab, Ctrl+P quick open, Ctrl+` focus bottom, Ctrl+B toggle sidebar |
| Language map | — | `.dart`, `.py`, `.md`, `.yaml`/`.yml`, `.json`, `.toml`, `.sh`, `.ps1` — default plain text |

**Freezed note:** Not currently in `pubspec.yaml`. Options: (a) add `freezed` + `freezed_annotation` + `build_runner` config, or (b) manual `@immutable` + `copyWith`. Recommend (a) for D3+ since editor state will grow.

**Exit criteria (D3):** Multiple files open; syntax highlight; dirty state + save; session restore after app restart; unsaved close prompts; quick open works; shortcuts work; `flutter analyze` clean.

---

## Migration / Reuse Map

| Existing | Use in IDE |
|----------|------------|
| `HostdControlClient` | Explorer read + grant roots |
| `HostDaemonPage` | Full settings; slim `HostPanel` in IDE sidebar footer |
| `HostFileBrowserPage` | Delegate listing to shared `ExplorerService` |
| `SandboxFileView` / diff widgets | **Not moved yet** — D6 diff review |
| `ChatPage` | Agent panel placeholder → embed or route `/chat` in secondary sidebar in D6 |
| `engineer_run_repository` | Task center D8 |
| `AuraPalette` / `AuraTypography` | Extend, don't replace |

---

## Testing & Verification

- **Unit:** `workspace_file_service` containment (shared test vectors); `editor_session_notifier` tab/dirty persistence; `path_containment` vectors pass on client and hostd.
- **Widget:** `DesktopShell` smoke; explorer tile expand; editor tab dirty/save.
- **Manual (Windows):** Pair hostd → grant repo root → explorer → edit → save → restart app → tabs restore; preference toggle Home/IDE.
- **CI:** Existing Linux job must pass (new code guarded by `AuraPlatform.isDesktopIde` or tree imports only from desktop routes). **Add Windows desktop build job** (non-blocking for D1–D3 merge if analyze/tests pass on Linux).

---

## Explicitly Out of Scope (D4–D10)

- Real PTY / `xterm` terminal (D4)
- Git UI, diff review panel, interactive consent tiers (D5–D6)
- Agent task center, planning view, context inspector (D6–D8)
- macOS/Linux IDE shell parity (enable by widening `isDesktopIde` when ready)
- Backend task lifecycle UI (map to server states when API exists)
- LSP, autocomplete, go-to-definition (D6+)
- Inline AI edits, multi-file AI (D6+)

---

## Suggested File Tree (New)

```
aura_client/lib/
  core/
    platform/aura_platform.dart
    security/path_containment.dart          # D0 shared utility
    design_system/aura_ide_density.dart     # D1 ThemeExtension
    routing/router.dart                     # D1 modified
  desktop/
    shell/                                  # D1
      desktop_shell.dart
      activity_bar.dart
      main_workspace.dart
      bottom_panel.dart
      status_bar.dart
      desktop_shortcuts.dart
    state/                                  # D1
      workspace_state.dart
      editor_state.dart
      layout_state.dart
      host_state.dart
      approval_state.dart
      desktop_application_state.dart
    events/                                 # D1
      ide_event.dart
      ide_event_bus.dart
    workspace/                              # D1–D2
      desktop_workspace_repository.dart
      desktop_workspace_notifier.dart
      workspace_welcome_page.dart
      workspace_manager_page.dart
      workspace_file_service.dart           # D3
    explorer/                               # D2
      explorer_panel.dart
      explorer_tree_tile.dart
      explorer_service.dart
    host/                                   # D2
      host_panel.dart
    editor/                                 # D1–D3
      editor_abstraction.dart               # D1
      editor_tab.dart                       # D3
      editor_tab_bar.dart                   # D3
      code_editor_view.dart                 # D3
      editor_session_notifier.dart          # D3
      quick_open_dialog.dart                # D3
      providers/
        flutter_code_editor_provider.dart   # D3 initial impl
  features/
    settings/presentation/desktop_preference_page.dart  # D1
    command_palette/command_palette.dart    # D1 modified
docs/architecture/015-desktop-ide.md        # D0
```

---

## Implementation Order (Revised)

1. **D0**: Architecture doc + ADR-015 + Roadmap Phase D + containment test vectors
2. **D1**: Drift v12 migration + `AuraIdeDensity` + `AuraPlatform` + `DesktopShell` + routing + desktop preference + editor abstraction + state notifiers + event bus + Windows CI job
3. **D2**: Workspace repository/notifier + welcome/manager + explorer panel + host panel extraction + shared explorer service
4. **D3**: `flutter_code_editor` + `highlight` deps + `EditorTab` (immutable) + tab bar + editor surface via provider + file I/O + quick open + shortcuts + persistence polish
5. **Tests + `flutter analyze`** on Linux and Windows

---

## Dependencies to Add

| Package | Purpose | Phase |
|---------|---------|-------|
| `flutter_code_editor` | Editor widget | D3 |
| `highlight` | Syntax highlighting | D3 |
| `freezed` + `freezed_annotation` | Immutable models (EditorTab, etc.) | D3 |
| `json_annotation` | Serialization for Drift JSON columns | D1 |

Add to `pubspec.yaml` in D1 (abstraction) or D3 (editor impl) as appropriate.

---

## Windows CI (`.github/workflows/client-ci.yml`)

```yaml
# Add to existing workflow or new workflow
windows-desktop:
  runs-on: windows-latest
  steps:
    - uses: actions/checkout@v4
    - uses: subosito/flutter-action@v2
      with: { flutter-version: '3.19.x', channel: 'stable' }
    - name: Get dependencies
      run: flutter pub get
    - name: Generate code
      run: dart run build_runner build --delete-conflicting-outputs
    - name: Analyze
      run: flutter analyze
    - name: Test
      run: flutter test
    - name: Build Windows
      run: flutter build windows --release
```

Non-blocking for D1–D3 merge; required for D4+.

---

## Key Differences from Original Plan

| Original | Updated |
|----------|---------|
| Forced Windows → `/ide` redirect | Explicit desktop preference (Home / IDE) |
| Drift tables in D2 | Drift v12 in D1 |
| No editor abstraction | `EditorProvider` abstraction in D1 |
| No state architecture doc | DesktopApplicationState + ownership matrix in D1 |
| No event model | `IdeEvent` stream in D1 |
| No containment utilities | Shared `path_containment.dart` + test vectors in D0 |
| No Windows CI | Windows desktop build job in D1 |
| `EditorTab` mutable | `EditorTab` immutable (freezed or manual) |
| D1–D3 only | D0 added; D4–D10 mapped |
| AI as sidebar | Three-surface UX (Workspace/Intelligence/Control) |

---

## Approval

This plan incorporates:
- ✅ Platform gate + dual shell
- ✅ No forced redirect; desktop preference
- ✅ Drift v12 in D1
- ✅ `AuraIdeDensity` ThemeExtension
- ✅ Editor abstraction layer
- ✅ Desktop state architecture (ownership matrix)
- ✅ IDE event model
- ✅ Shared containment utilities (UX only)
- ✅ Immutable `EditorTab`
- ✅ Windows CI job
- ✅ Three-surface UX vision
- ✅ D0 architecture phase
- ✅ Scope limited to D0–D3

Ready for implementation.