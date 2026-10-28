# UX Test Plan

Where UX behaviour is asserted, what each suite is for, and — equally important — what is *not*
covered, so an absent test is never mistaken for a passing one.

## How this is actually verified

The environment these changes are written in has no Flutter SDK preinstalled, but it can fetch
one: downloading the archive for the exact version `client-ci.yml` pins and running the toolchain
out of a scratch directory takes about two minutes, and gives the same `Dart 3.12.2` CI would use.

```bash
curl -sSL -o flutter.tar.xz \
  https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_3.44.6-stable.tar.xz
tar xf flutter.tar.xz && export PATH="$PWD/flutter/bin:$PATH"
cd aura_client && flutter pub get \
  && dart run build_runner build --delete-conflicting-outputs \
  && flutter analyze && flutter test
```

**Do this rather than assuming a client change is unverifiable.** Several commits in this
repository's history say a client change could not be checked locally; for `analyze` and `test`
that is not true.

It mattered more than usual for a while, because `Client CI` went weeks without being assigned a
runner. It executed again on 2026-10-28 and `analyze-and-test` came back green on the same numbers
the local runs had been reporting — but some runs that same day still died in 2–4 seconds with no
runner, so treat a red X there as a question rather than a verdict, and read the
`analyze-and-test` job rather than the run's overall tick.

What the local run *cannot* settle stays unsettled: it is a headless Linux host with no device, so
font fallback resolution per platform, screen-reader output, keyboard traversal as a human
experiences it, and every runtime performance figure remain **UNKNOWN**.

## Layout

```
aura_client/test/ux/          UX-specific suites, added by this transformation
aura_client/test/desktop/     pre-existing desktop state/service tests
aura_client/test/             pre-existing feature and design-system tests
```

## Suites

### `test/ux/design_tokens_test.dart` — Phase 1

Asserts the *shape* of the token system rather than specific pretty values, because the values are
a design decision and the shape is the contract:

- the spacing scale is exactly `4, 8, 12, 16, 24, 32, 48, 64`, in order and without gaps — a gap is
  the reason a call site invents its own value;
- `AuraRadius.dialog` / `.sheet` and the four `AuraElevation` semantic names resolve to steps that
  are already on their scales rather than to new magic numbers;
- `AuraTypography.code()` / `.terminal()` carry a non-empty fallback chain, and keep the monospace
  family when a caller overrides the colour;
- `terminal` leads tighter than `code`;
- `pending` is not an alias of `warning`, `info` or `brand`, is legible as text on `surface` in
  both themes, and survives `copyWith` and `lerp`.

### `test/design_system_contrast_test.dart` — pre-existing, extended

Iterates `AuraPalette.aaTextPairs` for both themes and fails below WCAG AA 4.5:1. `info` and
`pending` were added to that list in Phase 1; adding `info` is what surfaced F-12.

**Any new colour that will ever be used as text must be added to `aaTextPairs`.** A token outside
that list is unchecked, which is the actual finding behind F-12 — not the value, the gap in
coverage.

### `test/ux/editor_position_test.dart` — Phase 3 (F-01)

The arithmetic behind the status bar's editor readouts: 1-based line/column across LF and CRLF
buffers, a CRLF terminator counting as one column rather than two, an offset that outruns a buffer
being clamped rather than throwing inside `build()`, and line-ending detection returning `Mixed`
rather than picking a winner — and `null`, not a guess, for a buffer with no terminator at all.

### `test/ux/desktop_breakpoint_test.dart` — Phase 3 (F-06)

The responsive rules as a value rather than as pixels: the three width classes and their
boundaries, which panels dock and which float at each, and the width clamping — the editor's
420px floor, a panel's 180px floor, and the fact that two docked panels are clamped against each
other rather than independently (each can "fit" alone while together squeezing the editor out).

### `test/ux/command_palette_test.dart` — Phase 5 (F-07)

The palette's matching and selection rules as plain functions — token matching, the deliberate
*absence* of fuzzy matching, order preserved rather than ranked, selection skipping disabled
entries and stopping at the ends — and then the keyboard path end to end: Enter runs the
selection and closes, arrow keys drive the list rather than the caret in the search field (the
thing most likely to regress silently), Escape closes without running anything, an unavailable
command is listed with its reason and announced as unavailable, and a query matching nothing
offers its own way out.

### `test/ux/desktop_commands_test.dart` — Phase 5 (F-07)

The desktop command set against real state: ids unique, workspace and file commands listed but
unavailable with the right reason when there is no workspace or no open file, layout commands
always available, a toggle titled for what it will do rather than what it is, platform-correct
shortcut hints — and the invariant that **no command is ever disabled without a reason**, which
is the audit's recurring finding expressed as a test.

### `test/ux/state_views_test.dart` — Phase 11

That an empty state answers what / why / what now, that an error state can offer two ways out,
that a declared secondary action with no callback is not rendered as a dead button — and the
accessibility half: each tone paints a distinguishable icon colour *and* announces itself
("Error. It broke. Here is why."), neutral adds no prefix because nothing is wrong, offline reads
as offline rather than as a generic failure, and the whole thing is a live region that does not
also read its own children twice.

### `test/ux/failure_states_test.dart` — Phase 11 (rule 35)

Classification of the backend's `kind` slugs into distinct states — inference-down separated from
unreachable, three different kinds of "wait", auth / permission / budget / safety each on their
own — plus the status-code fallback for slugs this client does not know yet, and a non-API error
classified rather than thrown past.

Then the two things most worth pinning: **Retry appears exactly where retrying helps** (never on
a safety refusal or a permission error, and the caller passing `onRetry` cannot override that),
the compact form fits a narrow fixed-height slot without overflowing it (the shape it exists for)
while still announcing the full explanation a screen reader has no size limit for, and **the copy
keeps its promises** — unreachable does not claim to know whose fault it is,
inference-down says the Control Plane is up because that is the useful part, and a rate limit
names its wait rather than inventing one.

### `test/ux/sync_status_test.dart` — Phase 11 (rule 19's `syncing` / `stale`)

The phase fold as a pure function — parked changes override a *settled* sync, while a live
`syncing` / `offline` / `error` state wins over them, because mid-sync what is happening now is
more useful than what is stuck. Then the labels (`Sync failed`, not `Error`; singular vs plural
parked counts), that every phase has its own glyph so colour is never the only signal, and the
widget: that a parked change reads as "2 changes not synced" rather than "Synced", that hiding the
label on a narrow surface still leaves the status in a live-region announcement, that mid-sync
shows a spinner and no button at all rather than a disabled one, and that the view is read-only
when given no callback.

### `test/ux/editor_commands_test.dart` — Phase 4 (F-08)

Format / GoToDefinition / FindReferences / RenameSymbol / CodeAction throw `UnimplementedError`;
Save / Undo / Redo, which no-op for real reasons, still return normally. The second group exists
so a later tidy-up cannot quietly sweep them in with the first.

### `test/ux/activity_bar_test.dart` — Phase 3 (F-02, F-09)

Behaviour and accessibility of the primary navigation: a tooltip on every item (the bar is
icon-only); Go to File publishing `ShowQuickOpenRequested` with a workspace open, and rendering
*and announcing* as disabled without one; the bottom item named for the panel it toggles and
tracking its state; selection and enablement exposed to semantics rather than living only in a
brand tint and a 6px dot. Plus the attention badge: the problem count rendered where it is
visible with the bottom panel collapsed, the semantic label carrying the count *and* its noun
("Panel, 3 problems" — a bare number read aloud says nothing about what it counts), singular vs
plural, no badge at all for a clean workspace, and a large count capped to `99+` visually while
the exact figure still reaches a screen reader.

## Not covered

Listed so the gaps are explicit:

| Area | Status |
|---|---|
| `StatusBar` as a rendered widget | **NOT COVERED.** Its substance — the line/column and line-ending arithmetic — is covered as pure functions. A widget test needs `gitNotifierProvider`, `approvalNotifierProvider` and `hostdStatusProvider` stood up, the last of which polls a real daemon; doing that properly is its own change. |
| Keyboard traversal order across the shell | **NOT COVERED.** Phase 13. |
| Focus visuals | **PARTIALLY.** The activity bar now sets a focus colour; nothing asserts it is visible at the required contrast. Needs a golden, which needs a device. |
| Monospace fallback resolution | **UNKNOWN — needs a running app per platform.** The token is BUILT, not PROVEN: a headless Linux test host cannot show what Menlo or Consolas resolve to. |
| Responsive shell *rendering* | **NOT COVERED.** The rules are covered as a value (`desktop_breakpoint_test.dart`); nothing asserts the rendered tree at three surface sizes, because a `DesktopShell` widget test has to stand up the Explorer, the editor, the agent panel and the daemon polling behind them — far more surface than the layout rule under test. |
| Mobile command palette | **NOT COVERED**, and not migrated onto the shared component. It had no tests before this work and still has none; writing them is the prerequisite for that migration. |
| Escape dismissing a floating panel | **NOT COVERED.** The binding is registered only while a panel floats; asserting it needs the shell widget test above. |
| Streaming / agent-activity rendering | **NOT COVERED.** Phases 7–8. |
| Approval UX | **NOT COVERED**, and blocked: the approval payload does not carry reason, risk or reversibility, so there is nothing truthful to assert yet (F-03). |
| State restoration end to end | **NOT COVERED.** `test/desktop/layout_notifier_test.dart` covers layout persistence at the notifier level; nothing covers restore-after-restart as a user-visible flow. |
| `syncing` / `stale` | **COVERED** — built on the signals the sync layer already exposed (`SyncState`, `parkedOutboxCountProvider`) rather than on invented ones. Adopted in chat's header; other surfaces can now mount `AuraSyncStatusView` but none do yet. |
| Internet vs Control Plane unavailable | **NOT DISTINGUISHABLE**, by design rather than by omission — see the note at the end of `TRANSFORMATION_STATUS.md`. |
| Per-screen adoption of `AuraFailureView` | **DONE for `AsyncValue.error`** — 13 surfaces. Covered by the classifier's own tests plus an overflow assertion on the compact form; there is no per-screen widget test, since each is the same two-line swap. |
| Every runtime performance metric in the transformation brief | **UNKNOWN — needs instrumentation on a real device.** No number in this repository should be quoted for any of them until it is measured. |
