# Client design refresh — design

**Date:** 2026-11-17 · **Status:** approved (approach A) · **Scope:** `aura_admin/`, `aura_client/`

## Intent

Make the user-facing apps read as one professional, distinctive product. Three goals, from the
owner's own words:

1. **One brand** — the operator console must look like part of the same product as the app.
2. **Easier navigation** — fix where people get lost, not just colours.
3. **First impressions** — the screens people see first (sign-in, onboarding, Today, Copilot's
   empty state) carry the most visual weight.

## Non-goals / anti-goals

- No new UI framework or dependency (no Tailwind/shadcn in the console, no new Flutter packages).
- No behaviour, API or routing-path changes: every existing route keeps working, every e2e
  selector the console's Playwright suite uses (link names, `h1` levels, button labels, the
  "last input" in a confirm dialog) keeps matching.
- No contrast regressions: Flutter palette values stay WCAG-checked; new console tokens are
  derived from the same palette.

## Approaches considered

- **A (chosen):** one shared brand foundation, each app built on it — console takes over the
  Flutter palette (indigo→violet, Inter); console shell, components and login redesigned in plain
  CSS; Flutter "You" hub split into labelled groups and the wide-screen rail becomes a sidebar;
  first-seen screens polished; hard-coded colours moved to palette roles.
- **B:** port the console to Tailwind + a component kit. Fast modern look but adds dependencies,
  breaks test markup, and still leaves two separately designed apps.
- **C:** Flutter theme-only tweak. Cheapest; misses navigation and the console.

## Design

### 1. Shared brand
Console CSS custom properties mirror `AuraPalette.dark` (background `#0F0C1B`, surface `#17162B`,
surfaceVariant `#221F3B`, brand `#6366F1`→`#8B5CF6`, status colours) with a light theme via
`prefers-color-scheme` mirroring `AuraPalette.light`. Type: Inter with system fallback. Radius
scale 8/12/16, matching `AuraRadius`.

### 2. Console
Grouped icon sidebar (Operate: Overview, Providers, Models, Knowledge · Intelligence: Prompts,
Learning · Governance: Approvals, Audit, Access, Settings), sticky top bar with breadcrumb and a
⌘K button, account card at the sidebar foot, gradient brand mark. Refined cards, metric tiles,
tables, pills (with status dot), buttons, inputs, banners, palette dialog. Branded split-layout
login. Mobile: sidebar collapses to a top strip.

### 3. App navigation
"You" hub regrouped into Account / Intelligence / Engineer / System sections. Wide layouts use an
extended rail (labels beside icons) with the brand mark. Hard-coded colours in `features/` and
`desktop/` replaced with palette roles where they express a semantic role.

### 4. First impressions
Login, onboarding, Today header and Copilot empty state get brand gradient accents, clearer
hierarchy and better empty states, using existing design-system components.

## Verification
Console: `npm run build`, `npm run lint`, `npm run test:e2e`, screenshots. Client:
`flutter analyze`, `flutter test`, golden-free; counts quoted in the PR.
