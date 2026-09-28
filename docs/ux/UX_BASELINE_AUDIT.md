# AURA UX Baseline Audit — Phase 0

**Date:** 2026-09-28
**Scope of this pass:** `aura_client/` — the V2 design system (`lib/core/design_system/`), the
Desktop IDE shell (`lib/desktop/`), and the cross-cutting token/state conventions the rest of the
app inherits from them.
**Method:** static inspection of the checked-in source. Every finding below cites the file and
line it was read from; nothing is inferred from a screenshot or from how a feature "probably"
behaves. The findings themselves were read, not run — but the fixes made against them *were*
executed: see `UX_TEST_PLAN.md` for how, and for what still cannot be settled without a device.

> **Status discipline.** This document records what the code *is*, not what it should be. Where a
> claim could only be settled by running the app (perceived latency, large-repo tree performance,
> screen-reader output), it is marked `UNKNOWN — needs runtime evidence` rather than asserted.

---

## 0. What already exists (so the transformation does not rebuild it)

The repository is further along than a greenfield UX task would assume. Before proposing anything,
these are the parts that already work and must be *extended*, not replaced:

| Area | Where | State |
|---|---|---|
| Semantic color tokens, light + dark, as a `ThemeExtension` | `lib/core/design_system/aura_palette.dart` | Real. `AuraPalette.of(context)`, full `copyWith`/`lerp`. |
| WCAG AA contrast gate wired into the test suite | `aura_palette.dart:aaTextPairs` + `test/design_system_contrast_test.dart` | Real, and it fails CI on regression. |
| Spacing / radius / elevation / motion tokens | `lib/core/design_system/tokens.dart` | Real. `AuraMotion.respecting()` already honours `MediaQuery.disableAnimations`. |
| Type scale built from the palette | `lib/core/design_system/typography.dart` | Real (M3 scale over Google Fonts Outfit). |
| IDE density tokens, scoped to the desktop subtree only | `lib/core/design_system/aura_ide_density.dart` | Real, deliberately not registered globally so mobile is untouched. |
| 10 shared components | `lib/core/design_system/components/` | Button, card, dialog, empty state, list tile, section header, select tile, skeleton, snackbar, text field. |
| Desktop layout persistence (panel widths, visibility, open tabs, terminal scrollback) | `lib/desktop/state/state_notifiers.dart:634-749` | Real, debounced, per-workspace. |
| Approvals surfaced in the status bar with per-item approve/reject | `lib/desktop/shell/status_bar.dart` | Real, stacked rather than one blocking modal. |
| Host connection state distinguishes *not running* / *not paired* / *reconnecting* / *connected* | `status_bar.dart:48-75` | Real, and already a fix for a prior "always says connected" bug. |

Phases 1–2 of the transformation are therefore **gap-filling on an existing system**, not a
from-scratch design system build.

---

## 1. Findings

Severity key: `CRITICAL` (breaks trust or blocks a core task) · `HIGH` · `MEDIUM` · `LOW` ·
`INFORMATIONAL`.

---

### F-01 — The status bar displays fabricated editor state

```text
Screen / Component   Desktop IDE — status bar
Current UX           The bar always reads "main", "Ln 1, Col 1", "UTF-8", "LF" regardless of
                     the real repository, caret, or file.
Current impl         lib/desktop/shell/status_bar.dart:79-85 ("main" is a literal, commented
                     "placeholder"), :96-115 (cursor / encoding / line-ending literals).
UX problem           The status bar is the IDE's one always-visible source of truth. Three of its
                     six readouts are constants. A user on branch `feature/x` is told they are on
                     `main`; a user editing a CRLF file is told LF.
Severity             CRITICAL
User impact          Actively misleading in exactly the place a developer checks before a commit
                     or a push. This is the failure mode rule 46 names ("do not create fake
                     progress") applied to state rather than progress.
Consistency issue    GitPanel shows the real branch from `gitNotifierProvider`; the status bar
                     shows a literal. Two indicators of the same fact, one of them wrong.
Accessibility issue  A screen reader reads the fabricated value with the same confidence as a
                     real one.
Performance issue    None.
Recommended fix      Drive branch from `gitNotifierProvider.branch`; compute Ln/Col from the
                     active tab's `cursorPosition` offset against its buffer; derive the line
                     ending from the buffer. Keep UTF-8 only because `WorkspaceFileService`
                     genuinely reads and writes UTF-8 (`readAsString`/`writeAsString` defaults,
                     workspace_file_service.dart:17-32) — and say so in a comment so the next
                     reader does not mistake a true constant for another placeholder.
Affected files       lib/desktop/shell/status_bar.dart
Dependencies         gitNotifierProvider, editorNotifierProvider (both already in the shell).
Risk                 Low — read-only wiring to providers the shell already watches.
Validation           Widget test: given a git state on branch `feature/x` and a buffer whose
                     caret is at line 3, the bar renders `feature/x` and `Ln 3, Col n`.
```

**Resolved in this transformation:** yes — see Phase 3 commit.

---

### F-02 — Two activity-bar controls do not do what they say

```text
Screen / Component   Desktop IDE — activity bar
Current UX           "Search" is rendered enabled and does nothing when clicked. "Settings"
                     (gear icon, bottom of the bar) opens the Terminal/Output/Problems panel.
Current impl         lib/desktop/shell/activity_bar.dart:62 (`onTap: () {}, // D2 stub`),
                     :110-117 (Settings item calls `toggleBottomPanel()`).
UX problem           One dead control and one mislabeled control in the app's primary navigation.
                     A dead control teaches the user the app is unreliable; a mislabeled one
                     teaches them the wrong model of where settings live.
Severity             CRITICAL
User impact          Both are in the five-item global nav — the highest-traffic surface in the
                     product.
Consistency issue    Every other item in the bar toggles the panel it names.
Accessibility issue  `Semantics(button: true)` announces "Search, button" for a control that is
                     not actionable, and does not announce selected state for any item, so
                     selection is conveyed by colour + a 6px dot only.
Performance issue    None.
Recommended fix      Wire Search to the Quick Open flow that already exists
                     (`ShowQuickOpenRequested` on the IDE event bus, consumed by DesktopShell)
                     and label it for what it actually does. Relabel the gear item to the panel
                     it toggles. Add `Tooltip`s (the bar is icon-only), `selected:` semantics,
                     and a visible keyboard focus treatment.
Affected files       lib/desktop/shell/activity_bar.dart
Dependencies         ideEventBusProvider (already imported by the shell).
Risk                 Low.
Validation           Widget test: tapping the file-search item publishes ShowQuickOpenRequested;
                     every item exposes a tooltip and a selected semantic flag.
```

**Resolved in this transformation:** yes — see Phase 3 commit.

---

### F-03 — Approval prompts do not carry the information needed to decide

```text
Screen / Component   Desktop IDE — pending-approvals dialog
Current UX           Shows the command string (or a diff, for `write_files`) and the root path,
                     then Approve / Reject.
Current impl         lib/desktop/shell/status_bar.dart:119-200 (`_showApprovalsDialog`).
UX problem           Rule 16 requires Action / Target / Reason / Permissions / Risk /
                     Reversibility / Affected resources. The dialog supplies Action and Target
                     only. The user approves a side-effecting operation without being told
                     whether it can be undone.
Severity             HIGH
User impact          This is the security boundary of the whole Engineer feature. An
                     under-specified prompt trains click-through approval, which is worse than
                     no prompt.
Consistency issue    Built from a raw `AlertDialog` + inline `TextStyle`s rather than
                     `AuraDialog`; `fontFamily: 'Courier'` is hardcoded (see F-05).
Accessibility issue  No scope/duration choice ("Allow once" vs "Allow for workspace"), so the
                     only accessible path is repeated per-call approval.
Performance issue    None.
Recommended fix      An `ApprovalCard` component taking action, target, reason, risk,
                     reversibility and affected resources; three outcomes (Allow once / Allow
                     for workspace / Deny). **Blocked on backend:** the approval payload
                     (`approval.arguments`) carries `command`, `root`, `paths`, `diff_preview`
                     and nothing else — reason, risk and reversibility are not transmitted
                     today. Inventing them client-side would be fabrication. The protocol must
                     carry them first.
Affected files       lib/desktop/shell/status_bar.dart, aura_hostd approval protocol,
                     aura_backend approval emitter.
Dependencies         Protocol change across three apps.
Risk                 Medium — cross-app contract.
Validation           Approval payload schema test + widget test per field.
```

**Resolved in this transformation:** **NOT IMPLEMENTED — BLOCKED.** Documented in
`APPROVAL_UX_SPEC.md` as a protocol prerequisite. Client-side invention of a risk rating would be
exactly the fabrication rule 46 forbids.

---

### F-04 — 99 hardcoded colours and 296 raw font sizes outside the design system

```text
Screen / Component   Feature screens (tasks, finance, goals, health, calendar, projects,
                     analytics, automations, profile, life hub) and parts of desktop.
Current UX           Visual drift between screens built at different times.
Current impl         `grep -c "Color(0x"` outside lib/core/design_system → 99 occurrences.
                     `grep -c "fontSize:"` outside the design system → 296 occurrences.
                     Worst offenders by named-colour count: analytics_page.dart (20),
                     goals_page.dart (15), profile_page.dart (12), projects_page.dart (10).
UX problem           A literal colour cannot respond to theme. Every one of these is a
                     potential dark-mode contrast failure that the AA gate cannot see, because
                     the gate only covers tokens declared in `aaTextPairs`.
Severity             HIGH
User impact          Dark mode is inconsistent screen to screen; type scale drifts.
Consistency issue    This is the consistency issue.
Accessibility issue  Literal colours bypass the contrast gate entirely.
Performance issue    None.
Recommended fix      Mechanical migration, screen by screen, each with a contrast assertion
                     added to the gate. Not a single sweeping commit: 395 sites across ~40
                     files cannot be reviewed as one diff, and an unreviewed colour change is
                     how a dark-mode regression ships.
Affected files       ~40 files under lib/features/.
Dependencies         None.
Risk                 Low per screen, high in bulk — hence the phasing.
Validation           A lint-style test asserting no new `Color(0x` appears under lib/features/,
                     ratcheting the count down rather than allowing regression.
```

**Resolved in this transformation:** **PARTIALLY IMPLEMENTED.** Tokens to migrate *to* are now
complete (Phase 1). The per-screen migration is deferred and tracked.

---

### F-05 — `fontFamily: 'Courier'` in 44 places; no monospace token

```text
Screen / Component   Terminal, diff views, problems panel, quick open, status bar, debug
                     console, code preview, host panel, git panel, output panel, agent panel.
Current UX           Code and terminal output are requested in "Courier".
Current impl         44 literal `'Courier'` occurrences across 20 files.
UX problem           "Courier" is not a font that ships with Android or most Linux desktops.
                     Where it is absent Flutter falls back to the *default* family, which is
                     proportional — so terminal output and diffs lose column alignment on the
                     platforms where that matters most.
Severity             HIGH  (UNKNOWN severity on any specific device — needs runtime evidence)
User impact          Misaligned diffs and terminal output are hard to read and easy to
                     misread — in a surface whose whole job is reviewing a change before
                     approving it.
Consistency issue    Two of the eleven required typography roles (`code`, `terminal`) had no
                     token at all, so every call site invented one.
Accessibility issue  None directly.
Performance issue    None.
Recommended fix      Add `AuraTypography.monoFamily` + a platform fallback chain
                     (Menlo / Consolas / DejaVu Sans Mono / Courier New / monospace) and
                     `code()` / `terminal()` style factories; migrate call sites incrementally.
Affected files       lib/core/design_system/typography.dart + 20 call-site files.
Dependencies         None.
Risk                 Low for the token; each call-site migration is independent.
Validation           Golden/render test is the only real proof and needs a device — until then
                     the token is BUILT, not PROVEN.
```

**Resolved in this transformation:** **PARTIALLY IMPLEMENTED.** Token added (Phase 1); status bar
migrated (Phase 3); the other 19 files are deferred and tracked.

---

### F-06 — The Desktop shell has no responsive behaviour

```text
Screen / Component   DesktopShell
Current UX           Activity bar + primary sidebar + editor + secondary sidebar + bottom panel
                     are laid out at their persisted pixel widths at every window size.
Current impl         lib/desktop/shell/desktop_shell.dart:100-175 — a Row of SizedBoxes; no
                     LayoutBuilder, no MediaQuery width branch anywhere in the file.
UX problem           Rule 30 requires wide / standard / compact behaviour. At a narrow window
                     the editor is squeezed to whatever remains after 280 + 320 + 48 px of
                     chrome, and nothing collapses.
Severity             HIGH
User impact          UNKNOWN in degree — no runtime measurement was possible. The absence of
                     any width branch in the source is certain; the visual result is not.
Consistency issue    Mobile has a responsive shell; desktop does not.
Accessibility issue  Text scaling (`textScaleFactor`) compounds this — larger type in fixed-width
                     panels clips rather than reflows.
Performance issue    None.
Recommended fix      A `DesktopBreakpoint` derived in a LayoutBuilder at the shell root:
                     wide → all panels; standard → secondary sidebar collapses to an overlay;
                     compact → primary sidebar becomes a drawer, AI becomes full-screen.
                     Persisted widths stay the user's preference *within* a breakpoint.
Affected files       desktop_shell.dart, state_notifiers.dart (LayoutState).
Dependencies         None.
Risk                 Medium — touches the layout every desktop test renders through.
Validation           Widget tests at three surface sizes asserting which panels are mounted.
```

**Resolved in this transformation:** **NOT IMPLEMENTED** — scheduled for a dedicated Phase 3
commit; it is the largest single change in the shell and does not belong in the same diff as the
honesty fixes above.

---

### F-07 — The command palette is mobile-only; the desktop has no command system

```text
Screen / Component   Command palette
Current UX           Ctrl/Cmd+K opens a palette of 16 mobile route destinations plus a local
                     task/event search. Nothing in lib/desktop/ references it.
Current impl         lib/features/command_palette/command_palette.dart (291 lines); its only
                     importer is lib/core/routing/router.dart:15.
UX problem           Rule 11 requires one universal palette covering commands, files, symbols,
                     settings, Git actions, AURA actions, agent actions and workspaces. Desktop
                     has Quick Open (Ctrl+P, files only) and eight hardcoded shortcuts
                     (desktop_shortcuts.dart) with no discoverable list.
Severity             HIGH
User impact          Every desktop action that is not one of eight shortcuts or a visible
                     button is undiscoverable.
Consistency issue    Two different "search something" dialogs with different interaction models.
Accessibility issue  Keyboard-only users cannot reach actions that exist only as an icon
                     button in a panel toolbar.
Performance issue    None.
Recommended fix      Promote the palette to a shared component with a registry of
                     `CommandSource`s; desktop contributes file, git, agent and layout sources.
Affected files       command_palette.dart, desktop_shortcuts.dart, new registry.
Dependencies         Phase 2 component work.
Risk                 Medium.
Validation           Test per source: querying "branch" surfaces the Git action.
```

**Resolved in this transformation:** **NOT IMPLEMENTED** — Phase 5.

---

### F-08 — Editor capability commands are declared but silently no-op

```text
Screen / Component   Editor
Current UX           No user-visible effect — no UI currently invokes them.
Current impl         lib/desktop/editor/providers/flutter_code_editor_provider.dart:201-213 —
                     Format, GoToDefinition, FindReferences, RenameSymbol and CodeAction each
                     `break` with a TODO. Confirmed by grep that no widget dispatches them.
UX problem           The editor abstraction advertises five capabilities that do nothing. The
                     danger is the *next* change: wiring a context-menu item to one of these
                     produces a menu entry that silently fails.
Severity             MEDIUM (INFORMATIONAL today, becomes CRITICAL the moment one is exposed)
User impact          None today.
Consistency issue    An abstraction whose contract is not met by its only implementation.
Accessibility issue  None.
Performance issue    None.
Recommended fix      Have unimplemented commands throw `UnimplementedError` rather than
                     `break`, so wiring one up fails loudly in a test instead of quietly in
                     production — and rule 46's "no fake functionality" is enforced by the
                     type system rather than by memory.
Affected files       flutter_code_editor_provider.dart
Dependencies         None.
Risk                 Low.
Validation           Unit test asserting each unimplemented command throws.
```

**Resolved in this transformation:** **NOT IMPLEMENTED** — Phase 4.

---

### F-09 — Activity bar has no attention badges

```text
Screen / Component   Activity bar
Current UX           Pending approvals appear only in the status bar; problems count appears
                     only on the bottom panel's tab, which is hidden when the panel is closed.
Current impl         status_bar.dart:77-88; bottom_panel.dart:74-76.
UX problem           Rule 8 requires the user to always know "what requires attention". With
                     the bottom panel collapsed (the default — `showBottomPanel = false`,
                     desktop_application_state.dart:432) a new error is invisible.
Severity             MEDIUM
User impact          Silent diagnostics.
Consistency issue    Two different count surfaces, neither always visible.
Accessibility issue  Counts are not announced anywhere persistent.
Performance issue    None.
Recommended fix      Badge counts on the activity bar items, with semantics
                     ("Problems, 3 errors").
Affected files       activity_bar.dart
Dependencies         diagnosticsNotifierProvider, approvalNotifierProvider.
Risk                 Low.
Validation           Widget test per badge.
```

**Resolved in this transformation:** **NOT IMPLEMENTED** — Phase 6.

---

### F-10 — No `tests/ux/` suite; UX behaviour is untested

```text
Screen / Component   Whole client
Current UX           n/a
Current impl         47 test files; 5 touch the desktop, none assert navigation semantics,
                     keyboard traversal, focus order, empty/error state rendering, streaming
                     rendering, or state restoration.
UX problem           Rule 38 requires a UX test suite. Without one, every finding above can
                     regress silently.
Severity             MEDIUM
User impact          Indirect but compounding.
Consistency issue    n/a
Accessibility issue  No automated a11y assertions at all.
Performance issue    None.
Recommended fix      `test/ux/` with per-phase suites, starting with the shell.
Affected files       new.
Dependencies         None.
Risk                 None.
Validation           The suite is its own validation.
```

**Resolved in this transformation:** **PARTIALLY IMPLEMENTED** — `test/ux/` created with the
shell suite covering F-01 and F-02.

---

### F-11 — Design-token gaps against the required semantic set

```text
Screen / Component   Design system
Current UX           n/a
Current impl         tokens.dart, typography.dart, aura_palette.dart.
UX problem           Against rule 5's required set, these were missing:
                       spacing   — 64 (scale stopped at 48)
                       radius    — `dialog`, `sheet` (only sm/md/lg/xl/pill)
                       elevation — semantic names flat/resting/raised/floating
                                   (only none/low/medium/high)
                       colour    — `pending` (a queued/awaiting state distinct from `warning`)
                       type      — `code`, `terminal`
                     Roles the audit found already satisfied under a different name, which
                     should be documented rather than duplicated: primary→`brand`,
                     secondary→`surfaceVariant`, disabled→`textDisabled`,
                     textMuted→`textSecondary`.
Severity             MEDIUM
User impact          Indirect: each gap is a reason a call site invents its own value.
Consistency issue    Direct cause of F-04 and F-05.
Accessibility issue  A new colour token must enter the AA gate or it is unchecked.
Performance issue    None.
Recommended fix      Fill the gaps; document the aliases; extend `aaTextPairs`.
Affected files       tokens.dart, typography.dart, aura_palette.dart.
Dependencies         None.
Risk                 Low — additive. `AuraPalette` is constructed in exactly one file
                     (verified by grep across lib/ and test/), so adding a required field is
                     not a breaking change here.
Validation           The existing contrast gate picks up the new pair automatically.
```

**Resolved in this transformation:** yes — Phase 1.

---

### F-12 — The light theme's `info` colour was below AA for text (found while closing F-11)

```text
Screen / Component   Design system — AuraPalette.light
Current UX           n/a — not visible in shipped UI today.
Current impl         aura_palette.dart: light `info` was sky-600 (#0284C7), measuring 4.10:1 on
                     white. Its three siblings had all been darkened for the light theme
                     (success #047857, warning #B45309, danger #DC2626); `info` had not.
UX problem           `info` was never in `aaTextPairs`, so the contrast gate could not see it.
                     Its single use today (`life_hub_page.dart:163`) is an icon tint, held to
                     the looser 3:1 UI-component threshold — which it passes. Nothing shipped
                     was failing. The defect is that the *next* author to use `info` as text
                     would have shipped a violation with no gate to catch it.
Severity             MEDIUM
User impact          None today; latent.
Consistency issue    One of four status colours not treated like the other three.
Accessibility issue  The gate's coverage, not just the value.
Performance issue    None.
Recommended fix      sky-700 (#0369A1) — 5.93:1 on white — and add the pair to `aaTextPairs` so
                     it stays checked.
Affected files       lib/core/design_system/aura_palette.dart
Dependencies         None.
Risk                 Low — one icon tint becomes slightly darker.
Validation           The existing contrast gate, now that the pair is in it.
```

**Resolved in this transformation:** yes — Phase 1.

---

## 2. Findings by severity

| Severity | Findings |
|---|---|
| CRITICAL | F-01 (fabricated status bar), F-02 (dead + mislabeled nav controls) |
| HIGH | F-03 (thin approval prompts), F-04 (hardcoded colour/type), F-05 (no mono token), F-06 (no responsive shell), F-07 (no desktop command system) |
| MEDIUM | F-08 (silent no-op editor commands), F-09 (no attention badges), F-10 (no UX tests), F-11 (token gaps), F-12 (light `info` below AA for text) |
| LOW | — |
| INFORMATIONAL | The 10 existing components cover roughly a third of rule 6's list; the remainder (toasts, banners, tabs, toolbars, context menus, status badges, tree items, approval cards, agent steps, diff summaries, activity items) exist only as per-screen one-offs. Enumerated in `COMPONENT_GUIDE.md` as Phase 2 scope. |

## 3. Not audited in this pass

Named explicitly so the gaps are not mistaken for clean bills of health:

- `aura_client/lib/features/**` beyond the colour/type grep — mobile IA, voice, memory, Personal OS.
- `aura_admin/` (Next.js dashboard).
- Runtime behaviour of any kind: startup time, tree expansion on a large repository, streaming
  render cost, screen-reader output, keyboard traversal order. **All `UNKNOWN — needs runtime
  evidence`**, and no phase depending on them may be called complete from this environment.
