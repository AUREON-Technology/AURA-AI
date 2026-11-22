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
  - **Done 2026-11-22, before the 16:46 UTC manual deploy:** the trigger was deleted through
    Railway's API (`deploymentTriggerDelete`); read back, `AURA-Web` has no deployment triggers and
    keeps root directory `/` and the repository as its source. Observed after: PR #286 merged to
    `main` at 16:49:09 UTC and `AURA-AI` began building from it at 16:49:12 UTC, while `AURA-Web`
    got **no** new deployment and still served the published build at 16:49:26 UTC. One observation
    after one merge, not a standing guarantee; check Railway's deployment list after the next merge.
    To restore the old behaviour, reconnect a GitHub trigger on `main` in the dashboard.

## Owner setup (nothing here can be done from the repository)

**State on 2026-11-22 (evening, UTC; values are names or public identifiers, never secrets):**

| Step | State |
|---|---|
| 1. CORS | **Done.** Gateway `ALLOWED_ORIGINS` is now `https://aureon-ai.tech,https://web.aureon-ai.tech` (appended; the old entry kept). Preflight from the web origin answered 400 before and 200 with `access-control-allow-origin: https://web.aureon-ai.tech` after. The API is served at `api.aureon-ai.tech`; the apex `aureon-ai.tech` answers 404 "Application not found". |
| 2. Firebase | **Mostly done.** A Web app ("AURA AI Web Client") already existed in project `aura-ai-anas-gnedy`, so none was registered. `web.aureon-ai.tech` added to authorized domains; Email/Password enabled (email link and anonymous off); Google was already enabled; the five `FIREBASE_WEB_*` repository variables set. The gateway had **no** `FIREBASE_CREDENTIALS_JSON`, so it refused every Firebase sign-in (log: "Firebase Auth verification is disabled"; `POST /api/v3/auth/consumer/token` answered 401); a key for the project's Firebase Admin service account was created and set (customer-sign-in.md step 1) and the log now reads "Firebase Admin SDK successfully initialized". **Apple is not configured** (the provider answers `CONFIGURATION_NOT_FOUND`; needs a Services ID, Team ID, Key ID and key). The user import (customer-sign-in.md step 5) was **not run**. |
| 3. Deploy token | **Not done.** The repository has no secrets, `RAILWAY_WEB_TOKEN` included. The first publish was made by hand (below). |
| 4. `AURA_APP_BASE_URL` | **Done.** `https://web.aureon-ai.tech/app`. |
| 5. `AURA-Web` settings | **Root directory `/` and `aura_web/Dockerfile`: done. GitHub trigger: deleted** (see "How it is published"). The builder still reads Railpack; the Dockerfile is used through `RAILWAY_DOCKERFILE_PATH`. |

Setting the two gateway variables redeployed `AURA-AI` once, as did setting the credentials.

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

## Publish by hand (when Actions cannot run)

Used once, on 2026-11-22, while the GitHub billing block kept every workflow from starting. It
publishes exactly what the workflow would, from a checkout of `main`, with the owner's Railway login
(no `RAILWAY_WEB_TOKEN` needed). Windows with Git Bash:

```bash
git fetch origin main && git worktree add --detach ../aura-web-build origin/main && cd ../aura-web-build
export MSYS_NO_PATHCONV=1                   # else Git Bash turns "--base-href /app/" into a Windows path
export BUILD_COMMIT="$(git rev-parse HEAD)" # stamped into app-version.json (see the note below)
export FIREBASE_WEB_API_KEY=… FIREBASE_WEB_APP_ID=… FIREBASE_WEB_PROJECT_ID=… \
       FIREBASE_WEB_AUTH_DOMAIN=… FIREBASE_WEB_MESSAGING_SENDER_ID=…   # the repository variables
(cd aura_client && flutter pub get && dart run build_runner build --delete-conflicting-outputs)
SKIP_PUB_GET=1 scripts/build-web-app.sh aura_web/app-build
cat aura_web/app-build/app-version.json     # "commit" must be the SHA, not "unknown"

# Stage as the workflow does, in a clean directory outside the checkout:
mkdir -p ../stage/scripts && cp -r aura_web ../stage/aura_web && cp scripts/AURA-Desktop-Setup.cmd ../stage/scripts/
# If https://web.aureon-ai.tech/desktop-ide/download/latest.json answers 200, also copy the live
# installer and that file into ../stage/aura_web/build-assets/ after checking its SHA-256 against
# the manifest (the "Carry the live desktop installer" step of the workflow). On 2026-11-22 it
# answered 404, so nothing was carried.
cd ../stage && railway up --service AURA-Web --project <project id> --environment production --ci
```

Then check what "How it is published" lists: `app-version.json` names the commit, `/` redirects,
`/desktop-ide/install` answers 200, and `node scripts/web-app-smoke.js https://web.aureon-ai.tech`
passes (`npm i playwright && npx playwright install chromium` in a scratch directory, with
`NODE_PATH` pointing at it).

Two things went wrong on the first run. The `--base-href` mangling above failed the first build.
Then `build-web-app.sh` stamped `"commit":"unknown"` although `git rev-parse HEAD` worked from the
same shell; the cause was not found, so the file was rewritten by hand with the real SHA and
`builtAt` left as the script wrote it. `BUILD_COMMIT` is the script's own override, but the run
with it set has **not** been done; confirm the printed commit before deploying.

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

## What is proven, and what is not (updated 2026-11-22, evening)

On the scale in [`docs/status/README.md`](../status/README.md), **nothing here is PROVEN**: PROVEN
needs a committed live or real-device report, and the manual checklist has not been run. What
follows is what was **observed live** on 2026-11-22 (UTC), which is the evidence a report would be
written from.

**Published.** Built on the owner's machine from `main` at `5074875` (Flutter 3.44.6, the five
`FIREBASE_WEB_*` values) and deployed by hand with
`railway up --service AURA-Web --environment production --ci` at 16:46 UTC, because every GitHub
Actions job still fails in 2 to 4 s (annotation: "recent account payments have failed or your
spending limit needs to be increased", so CB-44 is a billing block, not a runner shortage). The
image built under `nginx -t`. No installer was published, so none was carried.

**Observed live after the deploy:**
- `/app/app-version.json` reports commit `5074875233cf…`, `builtAt` 16:45:26 UTC, backend
  `https://api.aureon-ai.tech`. (The build script stamped `unknown` on this run; the file was
  rewritten with the real SHA in the script's format before deploying. See "Publish by hand".)
- `/` answers 302 to `/app/`; `/app/` answers 200 with `no-cache` and the app's own CSP;
  `/app/invite`, `/app/desktop-sign-in`, `/app/index.html`, `/app/main.dart.js`, `firebase-sdk.js`,
  `flutter_bootstrap.js`, `drift_worker.js`, `sqlite3.wasm`, both `canvaskit.wasm` builds, the
  manifest, the version, asset and font manifests, the service worker and the four manifest icons
  answer 200; `/app` answers 301; `/app/nope` answers 404. `/desktop-ide/install`,
  `/desktop-ide/download/AURA-Desktop-Setup.cmd` and `/healthz` answer 200;
  `/desktop-ide/download/latest.json` answers 404 (no installer published).
- `node scripts/web-app-smoke.js https://web.aureon-ai.tech` (Playwright 1.65, headless Chromium):
  desktop-1280, phone-390 and invite-link all `ok`; the 1280 and 390 px screenshots show the login
  page with Google, Apple and email.
- A sweep of all 42 router paths, signed out, at 1280 px (each opened as `/app/#/<path>`, plus an
  unknown path): every one loaded the Flutter app with no console error, failed request, HTTP
  status of 400 or more, or CSP violation. 41 land on the login page; `/desktop-sign-in` shows
  "This sign-in link isn't valid" (correct with no parameters). **Pages behind sign-in were not
  looked at by this sweep.**
- Gateway: `/health/ready` healthy after each redeploy; the CORS preflight above; the `POST
  /api/v3/auth/consumer/token` answers 401 before the Firebase credentials were set and 200 after.
- **One real sign-in**, by the owner, with a Google account, in Chrome: the gateway logged
  `POST /api/v3/auth/consumer/token` 200 at 17:14:54 UTC, and the app showed You, Profile and
  Workspace (Personal) for that account (a screenshot, not a committed report). The sign-in
  resolved to the account's existing Google-subject row (`canonical_subject`), not a new one.
- The console's Access page then refused to assign that account a role ("no user with that
  email"): its row had the `…@unknown.local` placeholder email from earlier that day, and
  `get_or_create_user` never replaced it. Fixed in code by PR #295 (draft, tests local only, not
  CI-run); the production row was corrected by hand in Railway's query tool, after which two
  `PATCH /api/v3/admin/operators/<email>/role` calls answered 200 (17:27 UTC). The code fix is not
  deployed.

**Not proven:**
- The manual checklist (PR #288) has **not been run**: login at phone width on a real phone, You and
  Engineer panes, Arabic mirroring, the missing chat microphone and `/voice` redirect, the Engineer
  daemon page, an invite link with a real token, billing.
- **Apple and email/password sign-in** were not tried; Apple is not configured. Only Google was
  tried, once, on one account.
- Behaviour after sign-in beyond the profile page (chat, knowledge, memory, workspaces), and every
  page behind sign-in, has not been observed or checked by any sweep.
- The workflow path: `web-app-main-channel.yml`, `RAILWAY_WEB_TOKEN`, the live-installer handover
  and the two-workflow deploy have **never run** (billing block). The one publish was manual.
- That the GitHub trigger stays off across merges (one merge observed, above).
- The user import (customer-sign-in.md step 5), so existing accounts' continuity on a Firebase uid
  is unobserved beyond the one account above.
- Safari, Firefox, screen readers, Arabic rendering per OS, real-network performance of the ~6 MB
  `main.dart.js` plus CanvasKit, and Fawry checkout.

### Earlier record (2026-11-22, before the first publish)

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
