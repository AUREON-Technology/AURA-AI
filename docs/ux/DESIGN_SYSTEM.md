# AURA Design System

The single source of truth for colour, type, spacing, radius, elevation and motion in
`aura_client`. Import `package:aura_client/core/design_system/design_system.dart` — one barrel —
rather than reaching into individual token files.

**Rule:** a feature widget never writes a literal colour, a literal font size, or a literal
duration. If the value you need is not here, add it here.

---

## 1. Colour

Colours are semantic roles, not hues. They live on `AuraPalette`, registered as a Flutter
`ThemeExtension`, and are read with `AuraPalette.of(context)`. Light and dark are two instances of
the same class, which is what makes theming a config change rather than a per-screen rewrite.

### Roles

| Role | Token | Use |
|---|---|---|
| background | `background` | The window/page behind everything. |
| surface | `surface` | Cards, panels, sheets, dialogs. |
| surfaceVariant | `surfaceVariant` | A surface that needs to recede or separate — status bar, tool strips, secondary rows. Also the **secondary** button fill. |
| border | `border` | Hairlines and dividers. |
| primary | `brand` | The one accent. Buttons, active nav, focus. |
| — as text | `brandOnSurfaceText` | The brand hue shifted to pass AA as text/links. **Never use `brand` for text.** |
| onPrimary | `onBrand` | Content sitting on a `brand` fill. |
| textPrimary | `textPrimary` | Body and headings. |
| textSecondary | `textSecondary` | Supporting text. Serves the `textMuted` role — there is no separate muted token. |
| disabled | `textDisabled` | Disabled labels, placeholder glyphs. |
| success | `success` | Completed, passing, connected. |
| warning | `warning` | Something is wrong but recoverable. |
| error | `danger` | Failed, destructive. |
| info | `info` | Neutral notice. |
| pending | `pending` | **Queued / awaiting a decision.** Not a warning — nothing is wrong; it has not happened yet. Approvals waiting on the user, plan steps not started, syncs in flight. |

### Deliberate aliases (do not add duplicates)

The required semantic vocabulary names `primary`, `secondary`, `accent`, `textMuted` and
`disabled`. Those roles are already served, under the names above. Adding a second token for the
same role is how a design system starts to drift, so the mapping is documented rather than
duplicated:

```text
primary     → brand            (+ brandOnSurfaceText for text use)
secondary   → surfaceVariant   (fill) / textSecondary (text)
accent      → brand            AURA has one accent on purpose
textMuted   → textSecondary
disabled    → textDisabled
```

### The contrast gate

`AuraPalette.aaTextPairs` lists every (foreground, background, label) triple that must hold WCAG
AA 4.5:1. `test/design_system_contrast_test.dart` iterates it for both themes and fails the build
on regression.

**When you add a colour that will ever be used as text, add it to `aaTextPairs`.** A token outside
that list is unchecked.

Measured ratios for the pairs added most recently:

| Pair | Dark | Light |
|---|---|---|
| `pending / surface` | 9.59:1 | 7.10:1 |
| `info / surface` | 8.26:1 | 5.93:1 |

Adding `info` to the gate is what surfaced that the light theme's `info` was sky-600
(`#0284C7`, **4.10:1 on white — below AA**) while `success`, `warning` and `danger` had all been
darkened for light mode. It is now sky-700 (`#0369A1`). It was in use only as an icon colour, which
is held to the looser 3:1 UI-component threshold, so nothing was failing in shipped UI — but the
next author to use it as text would have shipped a violation with no gate to catch it.

---

## 2. Typography

Read styles from `Theme.of(context).textTheme`. The scale is built once per palette in
`AuraTypography.textTheme()` over Google Fonts Outfit.

| Role | `textTheme` slot | Size / weight |
|---|---|---|
| display | `displayLarge` / `displayMedium` | 34 / 28, w800 |
| headline | `headlineLarge` / `headlineMedium` | 24 / 20, w700 |
| title | `titleLarge` / `titleMedium` | 18 / 16, w600 |
| subtitle | `titleSmall` | 14, w600 |
| body | `bodyLarge` / `bodyMedium` | 16 / 14, w400 |
| bodySmall | `bodySmall` | 12, w400 |
| label | `labelLarge` / `labelMedium` | 14 / 12, w600 |
| caption | `labelSmall` | 11, w600 |
| **code** | `AuraTypography.code(palette)` | 12, monospace, 1.45 leading |
| **terminal** | `AuraTypography.terminal(palette)` | 12, monospace, 1.30 leading |

### Monospace

`AuraTypography.monoFamily` + `AuraTypography.monoFallback`, applied for you by `code()` and
`terminal()`.

Do **not** write `fontFamily: 'Courier'`. Courier ships with neither Android nor most Linux
desktops, and Flutter falls back to the *proportional* default family when a requested one is
missing — so a diff or a terminal silently loses column alignment on those platforms. The fallback
chain (`monospace` → Menlo → Consolas → DejaVu Sans Mono → Courier New → Courier) resolves on every
platform the client targets.

`code()` is for spans inside prose, paths, commands and diff bodies. `terminal()` is for blocks of
output, where the tighter leading fits more lines into a scarce panel.

> **Status:** the token is BUILT, not PROVEN. Confirming the fallback resolves as intended on each
> platform needs a running app on that platform, which this environment cannot do. 19 of the 20
> files carrying `'Courier'` literals are not yet migrated — see `UX_BASELINE_AUDIT.md` F-05.

---

## 3. Spacing

`AuraSpacing` — the whole scale, and nothing between the steps:

```text
xs    4      md   12      xl   24      xxxl   48
sm    8      lg   16      xxl  32      xxxxl  64
```

`xxxxl` (64) is for the vertical rhythm of full-page empty states and onboarding, where 48 reads as
cramped against a whole viewport of nothing.

On desktop, multiply by `context.ideDensity.spacingScale` — the IDE runs tighter than mobile, and
`AuraIdeDensity` is registered only under `DesktopShell` so mobile layouts are untouched.

## 4. Radius

```text
sm 8    md 12    lg 16    xl 24    pill 999
dialog → lg      sheet → xl
```

Prefer the semantic alias on the surface it names. Two dialogs written six months apart should not
have to remember which numeric step they agreed on.

## 5. Elevation

```text
flat → 0     resting → 2     raised → 6     floating → 12
```

Describes what the surface *is*: content flat on the background, a card resting on it, a menu
raised over the content it covers, a dialog floating over everything. The numeric names
(`none`/`low`/`medium`/`high`) remain for the theme's own use.

## 6. Motion

```text
AuraMotion.fast        120ms   micro-interactions — hover, press, badge
AuraMotion.standard    200ms   panels, dialogs, tab changes
AuraMotion.emphasized  400ms   a transition the user should notice
```

**Every animation call site must go through `AuraMotion.respecting(context, duration)`**, which
returns `Duration.zero` when the platform requests reduced motion. Motion communicates state
change; it is not decoration.

---

## 7. Components

`lib/core/design_system/components/`. Current set: `AuraButton`, `AuraCard`, `AuraDialog`,
`AuraEmptyState`, `AuraListTile`, `AuraSectionHeader`, `AuraSelectTile`, `AuraSkeleton`,
`AuraSnackbar`, `AuraTextField`.

That covers roughly a third of the target component set. The gaps — toasts, banners, tabs,
toolbars, context menus, status badges, tree items, approval cards, agent steps, diff summaries,
activity items — exist today only as per-screen one-offs and are Phase 2 scope. See
`COMPONENT_GUIDE.md`.
