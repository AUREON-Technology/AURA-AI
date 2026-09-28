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
that is not true, and it matters, because GitHub's `Client CI` has not been assigned a runner in
weeks — every run, on `main` as well as on PRs, fails in 2–4 seconds with no step ever starting.
Treating CI as the gate while CI cannot run means nothing is gated at all.

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
| Monospace fallback resolution | **UNKNOWN — needs a running app per platform.** The token is BUILT, not PROVEN: a headless Linux test host cannot show what Menlo or Consolas resolve to. |
| Responsive shell behaviour | **NOT COVERED** — the behaviour does not exist yet (F-06). |
| Command system | **NOT COVERED** — does not exist on desktop yet (F-07). |
| Streaming / agent-activity rendering | **NOT COVERED.** Phases 7–8. |
| Approval UX | **NOT COVERED**, and blocked: the approval payload does not carry reason, risk or reversibility, so there is nothing truthful to assert yet (F-03). |
| State restoration end to end | **NOT COVERED.** `test/desktop/layout_notifier_test.dart` covers layout persistence at the notifier level; nothing covers restore-after-restart as a user-visible flow. |
| Every runtime performance metric in the transformation brief | **UNKNOWN — needs instrumentation on a real device.** No number in this repository should be quoted for any of them until it is measured. |
