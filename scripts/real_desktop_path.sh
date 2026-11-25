#!/usr/bin/env bash
# Run the REAL desktop-path suite: the production gateway (uvicorn), a real aura_hostd child
# process paired through the real `auth pair` flow, and a real WebSocket between them
# (aura_backend/tests/e2e/test_desktop_path_real.py). Nothing on that path is mocked.
#
#   scripts/real_desktop_path.sh                  # the whole gated suite
#   scripts/real_desktop_path.sh -k Containment   # any pytest arguments pass through
#   AURA_REAL_DESKTOP_SLOW=1 scripts/real_desktop_path.sh   # + the five-minute approval-expiry test
#
# Needs: a Dart SDK on PATH (or DART_SDK_BIN=/path/to/dart-sdk/bin), a Python with the backend's
# locked dependencies (PYTHON=..., default `python`), git, perl. No Docker, no network service.
#
# The default `pytest` run never executes this suite: it skips without AURA_REAL_DESKTOP=1.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"
python="${PYTHON:-python}"

if [ -n "${DART_SDK_BIN:-}" ]; then
  export PATH="$DART_SDK_BIN:$PATH"
fi
command -v dart >/dev/null || { echo "dart not found: put it on PATH or set DART_SDK_BIN" >&2; exit 2; }
command -v git >/dev/null || { echo "git not found" >&2; exit 2; }
command -v perl >/dev/null || { echo "perl not found (the daemon needs it for process groups)" >&2; exit 2; }

echo "== building the daemon (dart pub get, dart compile exe)"
(
  cd "$repo/aura_hostd"
  dart pub get >/dev/null
  mkdir -p build
  dart compile exe bin/aura_hostd.dart -o build/aura_hostd >/dev/null
)
export AURA_HOSTD_BIN="$repo/aura_hostd/build/aura_hostd"

# uvicorn serves WebSockets only with the `websockets` package, which requirements.lock pins but a
# trimmed venv may lack. Install exactly that pin, hash-checked, into a side directory -- never
# into the venv, never a new dependency.
if ! "$python" -c "import websockets" 2>/dev/null; then
  side="${AURA_REAL_DESKTOP_LIBDIR:-$repo/aura_backend/.real_desktop_lib}"
  echo "== websockets missing: installing the locked pin into $side"
  req="$(mktemp)"
  awk '/^websockets==/{p=1} p{print} p&&!/\\$/{exit}' "$repo/aura_backend/requirements.lock" > "$req"
  "$python" -m pip install --quiet --require-hashes --no-deps --target "$side" -r "$req"
  rm -f "$req"
  export PYTHONPATH="$side${PYTHONPATH:+:$PYTHONPATH}"
fi

export AURA_REAL_DESKTOP=1
export OMP_THREAD_LIMIT="${OMP_THREAD_LIMIT:-1}"
cd "$repo/aura_backend"
echo "== running tests/e2e/test_desktop_path_real.py against the real daemon"
exec "$python" -m pytest tests/e2e/test_desktop_path_real.py -p no:cacheprovider -W ignore "$@"
