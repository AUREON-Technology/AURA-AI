# Plan: Mobile = Chat + Engineer; Desktop = IDE only

Status: DRAFT, awaiting approval. No code written yet.
Scope layer: `aura_client/` (Flutter). Backend, sync and data code are unchanged.

## Goal
- The mobile app (Android, iOS) is one app, named "AURA AI", with two modules: Chat (Copilot) and Engineer.
- The Windows desktop app is IDE-only.
- Life Hub and Today are hidden from navigation and routes. Their code stays.

## User decisions (do not re-ask)
Dated 2026-11-17.
1. One mobile app, "AURA AI". The Chat and Engineer modules live in it. It is not split into two store apps.
2. Platforms: Android and iOS (mobile), Windows (desktop).
3. Life Hub and Today: hide routes and navigation. Keep the code, tests and sync data. Do not delete.
4. Mobile keeps every other screen (Knowledge, Memory, Automations, Voice, Projects, Goals, Feedback, Models, Analytics, You). Only Today and Life Hub are removed.
5. Mobile bottom nav after the change: Copilot, Engineer, You.
6. Mobile Engineer tab contains: engineer run history, GitHub connection, desktop pairing (host daemon), host file browser. Engineer chat stays inside Copilot as it is today.
7. Desktop keeps only: sign-in, the IDE (`/ide`, `/ide/workspace`) and Settings (You). Nothing else is reachable.
8. The Engineer app idea (a separate Engineer install) is dropped.

## Defaults I chose (not asked; change if wrong)
- Mobile landing screen after sign-in and onboarding: Copilot (`/chat`). This replaces `/today`.
- Desktop landing screen: `/ide` (the IDE). This is what `resolveAppRedirect` already does when the IDE preference is on. It becomes the only landing.
- Desktop "You" screen: shows only what the desktop build allows (sign-out, server choice, appearance). Its sub-pages (Knowledge, Memory, Automations, etc.) are not routed on desktop.
- Old URLs `/today`, `/home`, `/life`, `/tasks`, `/calendar`, `/finance`, `/health` redirect to `/chat` on mobile and to `/ide` on desktop. They never show Life Hub or Today.
- Desktop-only code (`lib/desktop/`) stays as it is. Only routing and navigation change.

## Client (`aura_client/`)

### Platform gate
- Add one place that answers "is this the desktop build": `isDesktopIde` (`lib/core/platform/aura_platform.dart`) already means Windows. Use it for the desktop profile.
- Desktop profile: routes limited to login, onboarding, `/you` (its sub-routes unreachable), `/ide`, `/ide/workspace`.
- Mobile profile: everything except Today and Life Hub.

### Router (`lib/core/routing/router.dart`)
- `initialLocation`: `/chat` on mobile, `/ide` on desktop.
- `resolveAppRedirect`: landing becomes `/chat` on mobile, `/ide` on desktop. The `desktop_use_ide` preference no longer decides the landing; desktop is always IDE.
- Remove the `/today` and `/life` `GoRoute`s from the mobile shell. Keep the `HomePage` and `LifeHubPage` classes in code, unrouted.
- Legacy redirects (`/home`, `/tasks`, `/calendar`, `/finance`, `/health`) point to `/chat` on mobile and `/ide` on desktop.
- `ShellLayout._calculateSelectedIndex` and `_onDestinationSelected`: index 0 = Copilot, 1 = Engineer, 2 = You. Engineer routes (`/engineer-history`, `/github-connect`, `/engineer-daemon`, `/engineer-daemon/browse`) select the Engineer tab.
- Mobile nav: `NavigationBar` and `NavigationRail` get three destinations (Copilot, Engineer, You). Localized labels for "Engineer" must exist in every locale file.
- Desktop: no `ShellLayout` nav. Only `DesktopShell` wraps `/ide` routes.
- Remove `_RailShortcuts` entries for Engineer on desktop. On mobile the wide rail keeps the other shortcuts.

### Navigation surfaces that name Today
- `lib/features/command_palette/command_palette.dart`: remove the "Today" destination. Add "Engineer" on mobile.
- `lib/desktop/shell/desktop_commands.dart`: "Leave IDE (Open Today)" becomes a no-op or is removed on desktop, because there is no Today screen.
- `lib/desktop/shell/desktop_shell.dart` `onLeaveIde`: currently `/today`. On desktop there is nowhere else to go, so remove the action.
- `lib/features/settings/presentation/settings_page.dart`: the "Use Desktop IDE" toggle is unnecessary on desktop (always IDE) and meaningless on mobile. Hide it.
- `lib/features/onboarding/presentation/onboarding_page.dart`: `context.go('/today')` becomes the landing for the platform.

### Life Hub and Today code
- Keep `lib/features/life_hub/` and `lib/features/home/` as they are.
- Their `Finance`, `Health` and similar entries are unreachable, so their tests still run.

### Localization
- Any new visible string ("Engineer" tab label, if not already present) goes into every ARB file in `lib/l10n/`. Run `flutter gen-l10n` (or the project's generator).

### Tests
- `test/shell_ia_test.dart`: update to assert Copilot, Engineer and You on mobile, and that "Today" and "Life Hub" are absent.
- New test: desktop platform profile lands on `/ide`, and routes outside login/onboarding/you/ide redirect to `/ide`.
- New test: old URLs redirect to `/chat` (mobile) and `/ide` (desktop).
- Existing `test/life_hub_contrast_test.dart`, `test/ux/no_raw_error_text_test.dart` keep running unchanged; they test the hidden code directly.

## Backend
No change. Life Hub data sync continues on the server.

## Contract
No API change. The client's sync payloads are unchanged.

## Rollout order
1. Client change lands on the mobile build and the Windows build together, since both are the same Flutter codebase.
2. No backend deploy needed.

## Verification (Phase 7)
- `flutter analyze` (must be clean).
- `flutter test` (must pass; compare with baseline: 687 passed, 4 skipped in the latest status doc).
- `flutter gen-l10n` run, generated files committed if the project commits them.
- Migrations: none.
- Manual test checklist (to be produced after build).

## Out of scope
- Two store apps or any new app IDs.
- Deleting Life Hub, Today or any sync data.
- Changing the backend, the desktop IDE's internals, or `aura_hostd`.
- Web build.
- macOS and Linux builds (not requested).
