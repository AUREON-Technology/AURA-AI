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
- **Railway's own GitHub deploy is on, and it is not a publisher you want here.** Observed through
  Railway's API on 2026-11-22 (read-only), `AURA-Web` has a GitHub deployment trigger on `main`.
  - The first build from git failed. The merge of #288 (`aa0a391`, 15:53 UTC) and then the merge of
    #289 (`2ff6282`, 16:00 UTC) both ran with root directory `/aura_client` and **FAILED**
    (the first with `COPY aura_web/nginx/default.conf.template: not found`, although that file is
    tracked in git; the second's log was not read). After the owner set the root directory to `/`,
    the same commit `2ff6282` built and went **SUCCESS** at 16:02 UTC. So the root directory was the
    cause: `aura_web/Dockerfile` says to leave it empty, because its build context is the
    repository root.
  - The settings now: root directory `/`, `RAILWAY_DOCKERFILE_PATH=aura_web/Dockerfile`. The
    service's builder field still reads Railpack, but the build log shows the Dockerfile being used.
  - **The trigger is still on, and any merge to `main` starts a build**, even a docs-only one (#289
    did). A build from git has an empty `aura_web/app-build/` and `aura_web/build-assets/`, so it
    replaces the web app and the installer with their "not built yet" states until the next
    workflow deploy lands. The workflows are the only deploys that carry the app and the installer;
    keep this trigger off (setup step 5).

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
5. **The `AURA-Web` service settings in Railway** (Settings, then Source and Build):
   - **done (2026-11-22):** the root directory is `/` and the build uses `aura_web/Dockerfile`;
   - **still to do:** disconnect the GitHub trigger (or leave the repository connected with automatic
     deploys off), so the workflows are the only publishers. Until then, publishing the app (by the
     workflow or `railway up`) lasts only until the next merge to `main`, which rebuilds the service
     from git without it;
   - optional: set the builder to Dockerfile in the dashboard, so the settings say what the build
     does. Whether the workflow's `railway up` (which uploads a staged tree, not the git checkout)
     is affected by the root directory is not known: earlier CLI deployments show none, but that is
     not proof;
   - check the result: the latest deployment in Railway is `SUCCESS`, and
     `curl -fsS https://web.aureon-ai.tech/app/app-version.json` names the commit that was deployed.

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

Observed live on 2026-11-22 (16:02 UTC deployment, commit `2ff6282`, built by Railway from git):
`aura_web/Dockerfile` builds and runs (so the image, `nginx -t` included, is proven by Railway, not
only by a local nginx); `/` redirects to `/app/` (302); `/app/` answers 200 with the "not built yet"
page (no app has been published) under the app's own CSP and `no-cache`; `/app/invite` answers 200
(the deep-link fallback); `/desktop-ide/install` answers 200 with its new "Open the web app" link;
`/healthz` answers 200. `/app/app-version.json` and `/desktop-ide/download/latest.json` answer 404
(no app and no installer have been published).

**Not proven:** the web app itself on the live host (it has not been published: the only run of
`web-app-main-channel.yml` failed in seconds on the no-runner problem, CB-44); a real
Google/Apple/email sign-in (needs the owner's Firebase project); the Railway deploy from the
workflow (`railway up`) and the two-workflow handover; Fawry checkout; Arabic rendering on each OS;
screen readers; behaviour on Safari and Firefox (Chromium only); real-network performance of the
~6 MB `main.dart.js` plus CanvasKit.
