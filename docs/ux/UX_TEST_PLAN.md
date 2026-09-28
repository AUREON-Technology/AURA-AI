# UX Test Plan

Where UX behaviour is asserted, what each suite is for, and — equally important — what is *not*
covered, so an absent test is never mistaken for a passing one.

## Constraint that shapes all of this

There is no Flutter/Dart toolchain in the environment these changes are written in. **Nothing in
`aura_client/` has been executed locally.** Client CI (`flutter analyze --fatal-infos` +
`flutter test` + the Android build) is the only verification, and a phase is not complete until it
has passed there. Anything needing a *running app on a real device* — font fallback resolution,
screen-reader output, large-repo tree performance, perceived latency — cannot be settled by CI
either, and is called out below as such.

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

### `test/ux/activity_bar_test.dart` — Phase 3 (F-02)

Behaviour and accessibility of the primary navigation: a tooltip on every item (the bar is
icon-only); Go to File publishing `ShowQuickOpenRequested` with a workspace open, and rendering
*and announcing* as disabled without one; the bottom item named for the panel it toggles and
tracking its state; selection and enablement exposed to semantics rather than living only in a
brand tint and a 6px dot.

## Not covered

Listed so the gaps are explicit:

| Area | Status |
|---|---|
| `StatusBar` as a rendered widget | **NOT COVERED.** Its substance — the line/column and line-ending arithmetic — is covered as pure functions. A widget test needs `gitNotifierProvider`, `approvalNotifierProvider` and `hostdStatusProvider` stood up, the last of which polls a real daemon; doing that properly is its own change. |
| Keyboard traversal order across the shell | **NOT COVERED.** Phase 13. |
| Focus visuals | **PARTIALLY.** The activity bar now sets a focus colour; nothing asserts it is visible at the required contrast. Needs a golden, which needs a device. |
| Monospace fallback resolution | **UNKNOWN — needs a running app per platform.** The token is BUILT, not PROVEN. |
| Responsive shell behaviour | **NOT COVERED** — the behaviour does not exist yet (F-06). |
| Command system | **NOT COVERED** — does not exist on desktop yet (F-07). |
| Streaming / agent-activity rendering | **NOT COVERED.** Phases 7–8. |
| Approval UX | **NOT COVERED**, and blocked: the approval payload does not carry reason, risk or reversibility, so there is nothing truthful to assert yet (F-03). |
| State restoration end to end | **NOT COVERED.** `test/desktop/layout_notifier_test.dart` covers layout persistence at the notifier level; nothing covers restore-after-restart as a user-visible flow. |
| Every runtime performance metric in the transformation brief | **UNKNOWN — needs instrumentation on a real device.** No number in this repository should be quoted for any of them until it is measured. |
