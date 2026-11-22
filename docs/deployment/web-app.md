# The AURA web app (`https://web.aureon-ai.tech/app/`)

The Flutter client (`aura_client/`) built for the browser. Same code as the mobile app, so the
same screens, languages and fixes; the browser build differs only where a browser cannot do what a
phone does. Plan, decisions and what was tried: [`docs/plans/client-web-app_plan.md`](../plans/client-web-app_plan.md).

Status of what is proven, and what is not, is at the end.

## What it is, where it lives

| | |
|---|---|
| URL | `https://web.aureon-ai.tech/app/` (`/` redirects there; the desktop installer stays at `/desktop-ide/install`) |
| Host | the existing `AURA-Web` Railway service: static nginx from `aura_web/` (`aura_web/Dockerfile`) |
| Build | `scripts/build-web-app.sh` -> `aura_web/app-build/` (git-ignored) -> copied to `/app/` in the image |
| API | `https://api.aureon-ai.tech` (`BACKEND_URL`), through the browser, so CORS applies |
| Sign-in | Firebase (Google, Apple, email) -> `POST /api/v3/auth/consumer/token`, no device attestation, role `User` |

## How it differs from the mobile app

- **Wide windows** (content 840 px or wider after the navigation rail): **You** keeps its menu on one
  side and opens the chosen page on the other; **Engineer** shows the run list beside the open run;
  **Copilot** keeps its conversation sidebar and centers messages at a readable width. Right-to-left
  languages mirror the panes. Narrower windows and phones get the mobile screens. Only the web
  build splits; a wide tablet running the Android app does not.
- **Not available in a browser** (hidden or replaced by a notice): live voice (`/voice` goes back to
  the chat), the chat microphone, biometric unlock, store purchase (billing is Fawry), and the local
  desktop daemon (the Engineer page says to pair a computer in the desktop app; remote control of an
  already-paired computer still works).
- Data lives in the browser (IndexedDB through sqlite3 wasm) with its key backup in browser
  storage. That is weaker than a phone's Keystore/Keychain; clearing site data signs the person out
  and drops the local copy (it re-syncs on sign-in).

## Build it

```bash
# from the repository root, Flutter 3.44.6 on PATH
scripts/build-web-app.sh                 # -> aura_web/app-build/
scripts/build-web-app.sh /tmp/web-app    # -> elsewhere
```

Environment: `BACKEND_URL`, `APP_BASE_URL` (default `https://web.aureon-ai.tech/app`), the Firebase
web config (`FIREBASE_WEB_API_KEY`, `FIREBASE_WEB_APP_ID`, `FIREBASE_WEB_PROJECT_ID`, optional
`FIREBASE_WEB_AUTH_DOMAIN`, `FIREBASE_WEB_MESSAGING_SENDER_ID`). The script builds with
`--base-href /app/ --no-source-maps --no-web-resources-cdn`, stamps `app-version.json`
(`{commit, builtAt, backendUrl}`) and refuses output without `<base href="/app/">`. Without the
Firebase values it still builds, and web sign-in says it is unavailable.

Run the image locally:

```bash
scripts/build-web-app.sh
docker build -f aura_web/Dockerfile -t aura-web .          # runs `nginx -t` at build time
docker run --rm -e PORT=8080 -p 8080:8080 aura-web         # http://localhost:8080/app/
node scripts/web-app-smoke.js http://127.0.0.1:8080        # see "Smoke test"
```

With no build in `aura_web/app-build/`, `/app/` answers a plain "not built yet" page instead of a 404.

## How it is published

- `.github/workflows/web-app-main-channel.yml`: on a push to `main` touching `aura_client/`,
  `aura_web/` or the build script (or by hand): `flutter analyze` + `flutter test` as a gate, build,
  deploy with `railway up --service AURA-Web`, then check `/app/app-version.json` reports **this**
  commit, `/` redirects to `/app/` and the installer page still answers.
- **One service, two workflows.** `railway up` replaces the whole image. The desktop workflow
  (`desktop-main-channel.yml`) also publishes to `AURA-Web`, so each carries the other's half: the
  web workflow downloads the *live* installer and `latest.json`, checks the installer's SHA-256
  against that manifest, and ships both again; the desktop workflow builds the web app itself with
  the same script. Their deploy jobs share the concurrency group `web-service-deploy`.
  A queued desktop deploy can be superseded by a newer web deploy; the installer then stays at the
  last published build until the next desktop run. That is the cost of one service. If it proves
  fragile, the fallback is a separate Railway service for the app.
- `client-ci.yml` has a `build-web` job: every client change proves the web target still compiles
  with the published flags.
- **If `AURA-Web` is also connected to Railway's GitHub auto-deploy** (not verifiable from the
  repository; check the service's settings), that deploy builds the image from git, where
  `aura_web/app-build/` and `aura_web/build-assets/` are empty. It would replace the app, and the
  installer, with their "not built yet" states until the next workflow deploy lands. The desktop
  workflow's header already says Railway only *hosts* the result; keep auto-deploy off for this
  service so the workflows are the only publishers.

## Owner setup (nothing here can be done from the repository)

1. **CORS.** On the Railway gateway, `ALLOWED_ORIGINS` must include `https://web.aureon-ai.tech`
   (comma separated, no path). Without it the browser blocks every API call.
2. **Firebase.** In the console: register a *Web* app in the project the Android app uses; add
   `web.aureon-ai.tech` to Authentication -> Settings -> Authorized domains; enable the providers
   ([`customer-sign-in.md`](customer-sign-in.md) steps 1-3 and 5). Save the web app's config as
   GitHub repository **variables** (`FIREBASE_WEB_API_KEY`, `FIREBASE_WEB_APP_ID`,
   `FIREBASE_WEB_PROJECT_ID`, optionally `..._AUTH_DOMAIN`, `..._MESSAGING_SENDER_ID`). They are
   public identifiers, not secrets.
3. **Deploy token.** The repository secret `RAILWAY_WEB_TOKEN` (the desktop workflow needs the same).
4. **Backend `AURA_APP_BASE_URL`** = `https://web.aureon-ai.tech/app`, so server-built links match
   the client's `APP_BASE_URL`.

## Security policy of `/app/`

`aura_web/nginx/security-headers-app.conf` (the installer keeps its own `default-src 'none'`). Every
allowance names what needs it; the file is the reference. Two decisions worth knowing:

- **No inline script.** Flutter's own bootstrap and the splash are external files. The Firebase JS
  SDK is loaded by `web/firebase-sdk.js` (pinned to the version `firebase_core_web` supports;
  `test/web/web_shell_test.dart` fails when a `firebase_core` upgrade moves it) and
  `web/flutter_bootstrap.js` waits for it. Otherwise `firebase_core_web` injects inline scripts the
  policy would (rightly) block.
- **No third-party image hosts.** The Google "G" on the sign-in button was fetched from Wikimedia;
  in a browser it is the generic icon instead. Bundling Google's official asset would restore it.

When a change needs a new host, widen the file deliberately and run the smoke test: it fails on any
CSP violation.

## Smoke test

```bash
node scripts/web-app-smoke.js https://web.aureon-ai.tech ./shots
```

Loads the app in headless Chromium at 1280 and 390 px and with an invite link; fails on a splash
that never leaves, a CSP violation, a failed or >= 400 request, a console error, or an invite
token left in the address bar.

## Roll back

Re-run `web-app-main-channel.yml` (workflow_dispatch) on the last good commit, or redeploy the
previous deployment in Railway. To take the app down, remove the `AURA-Web` `app-build` copy from
the image (`/app/` then shows "not built yet") by deploying with an empty `aura_web/app-build/`.

## What is proven, and what is not (2026-11-22)

Proven here (headless Chromium, nginx 1.24 serving the real `aura_web` config, Flutter 3.44.6):
the app builds under `/app/`; boots at 1280 and 390 px with no CSP violation, failed request or
console error, with and without a Firebase config; the Firebase SDK loads, the sign-in click opens
Firebase's handler popup and its hosts load under the policy; an invite link opens the app and
removes its token from the address bar; `/` redirects, the installer page is untouched, a missing
file is a 404. `flutter analyze` clean and `flutter test` green (counts in the status note).

**Not proven:** a real Google/Apple/email sign-in (needs the owner's Firebase project); the image
built and run by Docker (no Docker daemon in the session: `nginx -t` and the routes were checked
with the same config on a local nginx instead); the Railway deploy and the two-workflow handover;
Fawry checkout; Arabic rendering on each OS; screen readers; behaviour on Safari and Firefox
(Chromium only); real-network performance of the ~6 MB `main.dart.js` plus CanvasKit.
