# Plan: Client Web App (the mobile app, in a browser)

Status: APPROVED 2026-11-22; implemented on `anas/hopeful-fermat-sz07xr`; awaiting the manual test (see "As built").
Scope layers: `aura_client/` (Flutter), `aura_web/` (static host), `.github/workflows/`, docs.
Backend: no code change; one owner config change (CORS origin).

## Goal

Customers open `https://web.aureon-ai.tech/app/` and use AURA in a browser with the same structure
as the mobile app: Copilot, Engineer, You, and every screen the mobile app keeps. One codebase, so
mobile fixes reach the web automatically. No new UI stack.

## User decisions (do not re-ask)

Dated 2026-11-22.

1. **Approach:** Flutter web from the existing `aura_client/` codebase. No separate React/Next app.
2. **Address:** `https://web.aureon-ai.tech/app/`, served by the existing `aura_web` service.
   The app is built with base href `/app/`.
3. **Unsupported-in-browser features:** hidden, or replaced by a short "Available in the mobile
   app" notice. Live voice, biometric unlock, in-app purchase and host-daemon pairing are not
   built for web. Billing on web is Fawry (already the SaaS plan).
4. **Layout:** desktop-style multi-pane on wide browsers for **Copilot, Engineer and You only**.
   Every other screen keeps the mobile structure, centered at a readable width. Feature set,
   navigation labels, English/Arabic, RTL and light/dark are the mobile app's.
5. **Deploy path:** a new GitHub workflow builds Flutter web and drops the output into the
   `aura_web` image (the `desktop-main-channel.yml` pattern), then deploys with the existing
   `RAILWAY_WEB_TOKEN`.
6. **Root page:** `https://web.aureon-ai.tech/` redirects to `/app/`. The desktop installer stays
   at `/desktop-ide/install`; the web app links to it from You -> Desktop.

## Evidence gathered (2026-11-22, `anas/hopeful-fermat-sz07xr` at `c346d52`)

- `flutter build web --release` already **succeeds** on Flutter 3.44.6 (75 s; 48 MB output of which
  37 MB is CanvasKit; `main.dart.js` 6.1 MB). The web target is not a port from scratch.
- The web target already has: Drift on wasm, web encryption-key backup, Firebase popup sign-in via
  `/api/v3/auth/consumer/token`, Fawry checkout allowed, store purchase off, live-voice mic and
  sink off, `NoStoreGateway`.
- The router shell is already responsive (bottom bar < 768 px, rail >= 768 px, extended >= 1100
  px). Copilot already has a wide `ChatSidebar`. Engineer (`/engineer-history`) and You (`/you`)
  are single-column.
- **Gaps found:** no web host and no CI web job; no web Firebase config (`Firebase.initializeApp()`
  without options throws on web and is swallowed, so web sign-in says it is unavailable);
  `web/index.html` and `manifest.json` still say "aura_client / A new Flutter project";
  `aura_web` nginx answers 404 for everything outside `/desktop-ide` under a
  `default-src 'none'` CSP; the backend has no web origin in `ALLOWED_ORIGINS`.
- **Bug that appears only under a sub-path:** `webDeepLinkLocation` compares `Uri.base.path` to
  `/invite` and `/desktop-sign-in`, so under `/app/` an invite link (`/app/invite?token=`) never
  matches, and `appBaseUrl()` returns the origin without `/app`, so `inviteLinkFor` builds
  `https://web.aureon-ai.tech/invite?token=...`, which would 404. Both must account for the base path.
- Baseline (to compare against, quoted from the 2026-11-17/18 status entry): `flutter analyze` clean,
  `flutter test` 830 passed, 4 skipped. A fresh baseline run on this branch is in progress and
  will replace these numbers before any code changes.

## Client (`aura_client/`)

### 1. Web identity and build
- `web/index.html`, `web/manifest.json`: name "AURA AI", real description, `lang`, theme and
  background colors matching `AuraThemeV2`, `display: standalone`, `orientation: any`
  (it says `portrait-primary` today), loading splash while Flutter boots.
- Build command (documented in the app README and used by the workflow):
  `flutter build web --release --base-href /app/ --no-source-maps --no-web-resources-cdn
  --dart-define=BACKEND_URL=... --dart-define=APP_BASE_URL=https://web.aureon-ai.tech/app`.
  `--no-web-resources-cdn` self-hosts CanvasKit so the CSP does not need `gstatic.com` scripts.
- Keep the default **hash URL strategy** (the invite-token clean-up in `browser_address_web.dart`
  depends on it), so deep links need no server rewrite.

### 2. Sub-path correctness
- `appBaseUrl()` (`core/config/app_links.dart`): on web, return origin + the `<base href>` path
  (without trailing slash) instead of the bare origin.
- `webDeepLinkLocation`: strip the base-href path from `Uri.base.path` before matching.
- Tests: `/app/invite?token=x` -> `/invite?token=x`; `/app/desktop-sign-in?state=s`; `/app/` -> null;
  root-hosted build (base `/`) still works; `inviteLinkFor` includes `/app`.

### 3. Firebase web configuration
- Web needs `FirebaseOptions`. They are public identifiers, not secrets. Read from
  `--dart-define` keys (`FIREBASE_WEB_API_KEY`, `..._APP_ID`, `..._PROJECT_ID`,
  `..._AUTH_DOMAIN`, `..._MESSAGING_SENDER_ID`) in a new `core/config/firebase_web_options.dart`;
  `main.dart` passes them on web only. With none set the app starts as today and sign-in shows its
  existing "isn't available right now" message (the CI preview build has no keys).
- **Owner supplies the values** (Firebase console -> web app). Stored as GitHub repository
  variables, not in the repo.

### 4. Hide what a browser cannot do
Audit each, add a `canXHere` provider (pattern: `chatHeaderShowsLiveVoice`,
`canCheckoutHereProvider`) and a test per control:
- Live voice entry (chat header / voice route): hidden on web; `/voice` redirects to `/chat`.
- Chat mic (voice note capture): hidden on web (`voice_recorder_button.dart`).
- Biometric unlock in `login_page.dart`: skipped on web.
- Store purchase / manage subscription: already `NoStoreGateway`; confirm the plan page shows Fawry only.
- Host-daemon pairing and "start daemon" (`host_daemon_page.dart`, `hostd_pairing_flow.dart`):
  web shows the remote-control view (status, approve, folder list from `/api/v3/host/folders`) and
  "Pair a computer in the desktop app" instead of pairing controls.
- IDE routes (`/ide`, `/ide/workspace`): already "not available" on web; confirm.
- Each hidden control uses a localized notice where an empty space would confuse.

### 5. Layout: multi-pane on wide browsers (Copilot, Engineer, You)
- New `core/design_system/` helper `AuraWideScaffold` / `AuraMasterDetail` (list pane 320-380 px,
  detail pane flexible, collapses to the existing mobile navigation below 1100 px).
- **Copilot** (`/chat`): already conversation list + chat; cap message width (~760 px), keep
  composer centered, no changes to chat logic.
- **Engineer** (`/engineer-history`): run list on the left; selected run detail on the right;
  GitHub connection and remote-control cards as header sections. Below 1100 px identical to mobile.
- **You** (`/you`): settings menu on the left; the selected page (Profile, Appearance, Plan,
  Knowledge, Memory, ...) in the right pane. Mobile keeps push navigation.
- Every other route: content centered with a max width (~840 px) in the shell, no layout change.
- RTL: panes mirror (list on the right in Arabic). Directional paddings use `EdgeInsetsDirectional`.
- Keyboard: existing Ctrl/Cmd+K palette works; add visible focus and Tab order for the panes.
- Router: `ShellLayout` stays the single shell; selecting an item in a pane updates the URL
  (`/engineer-history/:id` style or query) so refresh and back button work. No new top-level tabs.

### 6. Localization
- New strings ("Available in the mobile app", "Open the web app", pane empty states) in
  `app_en.arb` and `app_ar.arb` (appended, no reformatting), then `flutter gen-l10n`;
  generated files committed (the project commits them).

### 7. Tests (new, in `aura_client/test/`)
- Deep-link and base-path tests (above).
- `platform` gating tests for each hidden feature, run with `kIsWeb` forced via the existing
  `_isWeb` override pattern.
- Widget tests for the Engineer and You master-detail at 1280 px, 1000 px and 390 px widths, in
  English LTR and Arabic RTL.
- Firebase web options: present vs absent.
- `shell_ia_test.dart` still passes unchanged on mobile widths.

## Web host (`aura_web/`)

- `aura_web/nginx/default.conf.template`: add `location /app/` serving the Flutter output
  (`/usr/share/nginx/html/app/`), `try_files $uri $uri/ /app/index.html`; `location = /app` -> 301
  `/app/`; `location = /` -> 302 `/app/`. `/healthz` and all `/desktop-ide` locations unchanged.
- **Own security headers for `/app/`**, because the installer's `default-src 'none'` would block
  Flutter. New `security-headers-app.conf`: `default-src 'self'; script-src 'self' 'wasm-unsafe-eval'
  https://apis.google.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:;
  font-src 'self' data: https://fonts.gstatic.com; connect-src 'self' https://api.aureon-ai.tech
  wss://api.aureon-ai.tech https://fonts.gstatic.com https://fonts.googleapis.com
  https://identitytoolkit.googleapis.com https://securetoken.googleapis.com; frame-src
  https://*.firebaseapp.com https://accounts.google.com; worker-src 'self' blob:; base-uri 'self';
  frame-ancestors 'none'`. The installer's policy stays as it is. **The exact Firebase hosts are
  my reading of the Firebase popup flow and cannot be proven without the owner's Firebase config
  and a live sign-in; they are checked in the manual test.**
- Cache: `index.html`, `flutter_bootstrap.js`, `version.json`, `main.dart.js`: `no-cache`;
  `canvaskit/`, `assets/`, `icons/`: one day.
- `Dockerfile`: `COPY aura_web/build-assets/app/ /usr/share/nginx/html/app/`; build-time `nginx -t`
  stays. With no build present, `/app/` answers a plain "not built yet" page (like the installer
  page does today).
- `aura_web/site/desktop-ide/install.html` gets an "Open the web app" link (CSP-safe: plain anchor).
- Header comments in `Dockerfile` and `default.conf.template` updated (they say the service is
  only the installer page).

## Workflow (`.github/workflows/web-app-main-channel.yml`)

- Trigger: push to `main` touching `aura_client/**`, `aura_web/**` or the workflow; plus
  `workflow_dispatch` with a `backend_url` input.
- `build` (Ubuntu): pinned Flutter 3.44.6, `pub get`, `build_runner`, `flutter analyze`,
  `flutter test`, `flutter build web ...` (flags above), writes `app-version.json` (commit, time).
- `deploy-web`: copies to `aura_web/build-assets/app/`, `railway up` for the AURA-Web service with
  `RAILWAY_WEB_TOKEN`, then checks `https://web.aureon-ai.tech/app/version.json` reports this commit.
- `concurrency` queues and never cancels, like the desktop channel. It shares the Railway service
  with `desktop-main-channel.yml`: each deploy replaces `build-assets`, so **each workflow must
  also fetch the other's last artifact**, or a web deploy would erase the installer (and the reverse).
  Plan: both workflows pull the previous live `/desktop-ide/download/*` or `/app/*` from the site
  before `railway up`. This coupling is the main risk of using one service (see Risks).
- Also add a **`build-web` job to `client-ci.yml`** (build only, no deploy) so every client PR
  proves the web target still compiles.

## Contract

- No API change. Web uses existing `/api/v3/*` and `/api/v3/auth/consumer/token`.
- Older clients: unaffected.

## Backend / owner configuration (no code)

Needed before the web app works for people; each is an owner action I cannot do:
1. Railway gateway: `ALLOWED_ORIGINS` += `https://web.aureon-ai.tech`.
2. Firebase console: Authorized domains += `web.aureon-ai.tech`; web app registered; the five web
   config values saved as GitHub repository variables.
3. Everything in `docs/deployment/customer-sign-in.md` steps 1-3 and 5 (credentials, providers,
   user import), which are already required for any customer sign-in.
4. Confirm `RAILWAY_WEB_TOKEN` exists (the desktop workflow needs it too).

A handoff note goes into `docs/deployment/customer-sign-in.md` §4 (web section) and a new
`docs/deployment/web-app.md` (build, deploy, CSP, CORS, rollback).

## Rollout order

1. Client + `aura_web` + workflows merge to `main` (CI build-only first).
2. Owner: CORS origin, Firebase web app, repo variables.
3. Run the new workflow with `workflow_dispatch`; check `/app/version.json`.
4. Manual test (Phase 7). Only then link it publicly.

## Verification

Automated (exact commands, from `aura_client/`):
```bash
flutter pub get && dart run build_runner build --delete-conflicting-outputs
flutter gen-l10n
flutter analyze
flutter test
flutter build web --release --base-href /app/ --no-source-maps --no-web-resources-cdn \
  --dart-define=BACKEND_URL=https://api.aureon-ai.tech
docker build -f aura_web/Dockerfile -t aura-web .        # from repo root; runs nginx -t
```
Plus: serve the image locally and fetch `/`, `/app/`, `/app/version.json`, `/desktop-ide/install`,
`/healthz`; and a headless Chromium smoke test with Playwright (already installed) that loads
`/app/`, checks no console CSP violations, no failed asset requests, and that the login screen
renders at 1280 px and 390 px.
What this host cannot prove: real Google/Apple/email sign-in, Firebase popup CSP hosts, Fawry
checkout, Arabic font rendering on every OS, screen-reader output, real-device performance.

## Risks

- **One service, two deploy workflows.** Each `railway up` replaces `build-assets`. Mitigated by
  each workflow re-fetching the other's live files; if that proves fragile the fallback is a
  separate Railway service for the app (a DNS/service owner action).
- **Bundle size:** 6 MB `main.dart.js` + 744 KB wasm + ~1 MB of canvas assets first load. Acceptable
  for a logged-in app; the CanvasKit variant is chosen by the loader.
- **Outfit font** is fetched at runtime from Google Fonts on web (`google_fonts`); the CSP allows
  it. Bundling the font is a later privacy improvement, out of scope.
- **Client CI has not run since 2026-10-31**; `main` is unprotected and Railway auto-deploys every
  push. The new `build-web` job and local runs are the only gate. Results are quoted from local
  runs and labeled as such.
- Browser storage holds the Drift DB and its key backup (IndexedDB / localStorage). Weaker than a
  device keystore; recorded here, not changed.

## Out of scope

Web live voice; browser microphone capture; PWA offline mode and push notifications; separate
React/Next app; marketing landing site; multi-pane for Knowledge, Memory, Automations, Projects,
Goals, Models; SEO/SSR; bundling the Outfit font; account linking screens.


## As built: where it differs from the plan above (2026-11-22)

Found while building and testing; each is a decision, not drift.

- **Build output dir** is `aura_web/app-build/`, not `build-assets/app/`: the Dockerfile copies all of
  `build-assets/` into `/desktop-ide/download/`, which would have shipped the 48 MB app twice.
- **Engineer selection is page state, not the URL.** There is no per-run route or fetch-one endpoint;
  the list is loaded whole. You's pages already have URLs, so refresh and Back work there.
- **You's split is applied by the shell** (`_WebContentFrame`) to every route that the navigation
  already treats as part of You (Knowledge, Memory, Automations, Models, ...), not only `/you`'s own
  pages. Opening them from You replaces the detail pane (`go`) instead of stacking (`push`).
- **Firebase SDK is loaded by our own page** (`web/firebase-sdk.js` + a custom
  `web/flutter_bootstrap.js`), not injected by `firebase_core_web`: its inline script is blocked by
  the CSP, and hashing it would break on every SDK upgrade. A test pins the version.
- **CSP additions found by running it** (not guessed): `accounts.google.com` (Google Identity
  Services script, styles), `*.googleusercontent.com` (profile photos). The Google "G" image is
  fetched from Wikimedia on mobile; on web it is the generic icon (no third-party image host).
- **Deep links need a fallback** the plan missed: `/app/invite` and `/app/desktop-sign-in` are real
  paths, so nginx serves `index.html` for exactly those two.
- **The two-workflow problem** is solved by each deploy carrying the other's half (above), with a
  shared concurrency group, rather than the plan's "each re-fetches the other's live files": the
  app cannot be re-fetched from a live site (no file list), so the desktop deploy builds it.
- Added: `scripts/build-web-app.sh` (one build for CI, the web workflow and the desktop deploy),
  `scripts/web-app-smoke.js` (headless-browser check), `docs/deployment/web-app.md`.
