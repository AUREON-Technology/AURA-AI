#!/usr/bin/env bash
# Build the AURA web app (the Flutter web build of aura_client) into aura_web/app-build, the
# directory aura_web/Dockerfile copies to /app/ in the web image.
#
#   scripts/build-web-app.sh [output-dir]
#
# Used by .github/workflows/web-app-main-channel.yml, by client-ci.yml's build-web job and by the
# desktop workflow's deploy job (which publishes to the same service and so must carry the app
# too). Needs Flutter on PATH (CI pins 3.44.6).
#
# Environment (all optional):
#   BACKEND_URL        API the app talks to.            default https://api.aureon-ai.tech
#   APP_BASE_URL       where the app is served from.    default https://web.aureon-ai.tech/app
#   FIREBASE_WEB_API_KEY, FIREBASE_WEB_APP_ID, FIREBASE_WEB_PROJECT_ID,
#   FIREBASE_WEB_AUTH_DOMAIN, FIREBASE_WEB_MESSAGING_SENDER_ID
#                      the Firebase web app (public identifiers). Without the first three the
#                      build still succeeds and web sign-in reports it is unavailable.
#   SKIP_PUB_GET=1     the caller already ran `flutter pub get` and build_runner.
#   BUILD_COMMIT       commit stamped into app-version.json. default: GITHUB_SHA or git HEAD.
#
# The app lives under /app/, so it is built with --base-href /app/. CanvasKit is bundled
# (--no-web-resources-cdn), so the page loads no renderer from a CDN and its CSP stays narrow.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="${1:-${REPO_ROOT}/aura_web/app-build}"

BACKEND_URL="${BACKEND_URL:-https://api.aureon-ai.tech}"
APP_BASE_URL="${APP_BASE_URL:-https://web.aureon-ai.tech/app}"
COMMIT="${BUILD_COMMIT:-${GITHUB_SHA:-$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)}}"

case "${BACKEND_URL}" in
  https://*) ;;
  *) echo "BACKEND_URL must be https:// for a web build served over https (got '${BACKEND_URL}')" >&2; exit 1 ;;
esac

defines=("--dart-define=BACKEND_URL=${BACKEND_URL}" "--dart-define=APP_BASE_URL=${APP_BASE_URL}")
for key in FIREBASE_WEB_API_KEY FIREBASE_WEB_APP_ID FIREBASE_WEB_PROJECT_ID \
           FIREBASE_WEB_AUTH_DOMAIN FIREBASE_WEB_MESSAGING_SENDER_ID; do
  value="${!key:-}"
  if [[ -n "${value}" ]]; then
    defines+=("--dart-define=${key}=${value}")
  fi
done
if [[ -z "${FIREBASE_WEB_API_KEY:-}" || -z "${FIREBASE_WEB_APP_ID:-}" || -z "${FIREBASE_WEB_PROJECT_ID:-}" ]]; then
  echo "::warning::No Firebase web config: this build starts, but web sign-in will say it is unavailable (docs/deployment/customer-sign-in.md section 4)."
fi

cd "${REPO_ROOT}/aura_client"
if [[ "${SKIP_PUB_GET:-}" != "1" ]]; then
  flutter pub get
  dart run build_runner build --delete-conflicting-outputs
fi

flutter build web --release \
  --base-href /app/ \
  --no-source-maps \
  --no-web-resources-cdn \
  --output "${OUT_DIR}" \
  "${defines[@]}"

# What the deploy job (and a person) reads to know which build is live. Not the Flutter
# version.json, which carries no commit.
built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '{"schema":1,"commit":"%s","builtAt":"%s","backendUrl":"%s"}\n' \
  "${COMMIT}" "${built_at}" "${BACKEND_URL}" > "${OUT_DIR}/app-version.json"

if [[ ! -f "${OUT_DIR}/index.html" || ! -f "${OUT_DIR}/main.dart.js" ]]; then
  echo "the build did not produce index.html and main.dart.js in ${OUT_DIR}" >&2
  exit 1
fi
if ! grep -q '<base href="/app/">' "${OUT_DIR}/index.html"; then
  echo "index.html does not have <base href=\"/app/\">: the app would not load from /app/" >&2
  exit 1
fi
echo "Web app built into ${OUT_DIR} (commit ${COMMIT})."
