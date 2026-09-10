#!/usr/bin/env bash
# Build and run the full desktop Engineer stack on THIS machine (Linux, or macOS when run on
# Darwin) -- aura_hostd (the local daemon that gives the Engineer agent real file/terminal
# access on this PC, ADR-011) plus the aura_client desktop app, wired to talk to the production
# backend. Run this ON THE MACHINE you want the Engineer agent to act on, not on a server --
# aura_hostd's workspace roots and containment are local to wherever it runs.
#
# What this script does NOT do: it does not pair the daemon or grant it a workspace root. Those
# are one-time, in-app steps (see the printed instructions at the end) -- ADR-011 deliberately
# has no bundled/auto-discovered aura_hostd binary or auto-pairing, the same disclosed-until-
# configured shape the backend's own OAuth client id used before it was registered.
#
# Usage:
#   ./scripts/run_desktop_stack.sh              # build aura_hostd + aura_client, then run both
#   ./scripts/run_desktop_stack.sh --skip-build  # reuse whatever was built last time
#   ./scripts/run_desktop_stack.sh --hostd-only  # build/run only aura_hostd (e.g. to re-pair
#                                                 # without relaunching the GUI)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CLIENT_DIR="$REPO_ROOT/aura_client"
HOSTD_DIR="$REPO_ROOT/aura_hostd"

SKIP_BUILD=0
HOSTD_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    --hostd-only) HOSTD_ONLY=1 ;;
    -h|--help)
      sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "ERROR: unrecognized argument: $arg (use --help)" >&2
      exit 1
      ;;
  esac
done

command -v flutter >/dev/null 2>&1 || {
  echo "ERROR: 'flutter' not found on PATH. Install the Flutter SDK first:" >&2
  echo "       https://docs.flutter.dev/get-started/install" >&2
  exit 1
}
command -v dart >/dev/null 2>&1 || {
  echo "ERROR: 'dart' not found on PATH. It ships inside the Flutter SDK -- make sure" >&2
  echo "       Flutter's bin/ directory (which provides both 'flutter' and 'dart') is on PATH." >&2
  exit 1
}

HOSTD_BIN="$HOSTD_DIR/build/aura_hostd"
HOSTD_LOG="$HOSTD_DIR/build/aura_hostd.log"

if [ "$SKIP_BUILD" -eq 0 ]; then
  echo "=========================================================="
  echo "Building aura_hostd..."
  echo "=========================================================="
  mkdir -p "$HOSTD_DIR/build"
  (cd "$HOSTD_DIR" && dart pub get && dart compile exe bin/aura_hostd.dart -o "$HOSTD_BIN")
fi
[ -x "$HOSTD_BIN" ] || {
  echo "ERROR: aura_hostd binary not found at $HOSTD_BIN -- run without --skip-build first." >&2
  exit 1
}

if [ "$HOSTD_ONLY" -eq 0 ] && [ "$SKIP_BUILD" -eq 0 ]; then
  echo "=========================================================="
  echo "Building aura_client (desktop, production backend)..."
  echo "=========================================================="
  "$SCRIPT_DIR/deploy_client_desktop.sh"
fi

if [ "$(uname)" == "Darwin" ]; then
  CLIENT_BIN="$CLIENT_DIR/build/macos/Build/Products/Release/aura_client.app/Contents/MacOS/aura_client"
else
  CLIENT_BIN="$CLIENT_DIR/build/linux/x64/release/bundle/aura_client"
fi
if [ "$HOSTD_ONLY" -eq 0 ] && [ ! -x "$CLIENT_BIN" ]; then
  echo "ERROR: aura_client desktop binary not found at $CLIENT_BIN -- run without --skip-build first." >&2
  exit 1
fi

echo "=========================================================="
echo "Starting aura_hostd..."
echo "=========================================================="
"$HOSTD_BIN" run >"$HOSTD_LOG" 2>&1 &
HOSTD_PID=$!

cleanup() {
  if kill -0 "$HOSTD_PID" 2>/dev/null; then
    echo "Stopping aura_hostd (pid $HOSTD_PID)..."
    kill "$HOSTD_PID" 2>/dev/null || true
    wait "$HOSTD_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

sleep 2
if ! kill -0 "$HOSTD_PID" 2>/dev/null; then
  echo "ERROR: aura_hostd exited immediately. Log:" >&2
  cat "$HOSTD_LOG" >&2
  exit 1
fi
echo "aura_hostd running (pid $HOSTD_PID). Log: $HOSTD_LOG"

echo "=========================================================="
echo "One-time setup inside the app (skip if already paired):"
echo "  1. Sign in with Google."
echo "  2. Open the Host Daemon screen (toolbar icon on Engineer history) -- the status card"
echo "     should already show it running (this script started it), so do NOT tap Start there"
echo "     too, or you'll spawn a second aura_hostd process. Set the executable path field to:"
echo "       $HOSTD_BIN"
echo "     anyway, so the app remembers it for a future launch where it does the starting."
echo "  3. Tap Pair, and approve the code from this same signed-in session."
echo "  4. Add a workspace root (a folder on this machine) and, optionally, Trust it"
echo "     to skip per-command confirmation inside that folder."
echo "=========================================================="

if [ "$HOSTD_ONLY" -eq 1 ]; then
  echo "aura_hostd is running -- launch aura_client separately, or Ctrl-C to stop it."
  wait "$HOSTD_PID"
else
  echo "Launching aura_client..."
  "$CLIENT_BIN"
fi
