#!/usr/bin/env bash
# AURA AI Flutter Client Desktop Build Script (Bash version)
# Run from anywhere. Resolves BACKEND_URL from AURA_DOMAIN in deploy/.env, same as
# deploy_client.sh. Separate script (not a mode of deploy_client.sh) because the desktop
# targets need platform-specific system libraries the mobile/web build doesn't, and because
# `aura_hostd` -- the daemon the desktop app supervises (see docs/architecture/011-desktop-
# engineer-agent.md) -- is not bundled: a person still configures its executable path
# themselves, so this script does not attempt to build or embed it.
#
# Builds Linux always; builds macOS only when run on Darwin (parallels deploy_client.sh's own
# iOS-on-Darwin-only gate). Windows desktop is built by deploy_client_desktop.ps1 instead --
# Flutter cross-compiles neither macOS nor Windows desktop builds from Linux.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CLIENT_DIR="$REPO_ROOT/aura_client"
ENV_FILE="$REPO_ROOT/deploy/.env"

# shellcheck source=deploy/scripts/client_backend_url.sh
source "$REPO_ROOT/deploy/scripts/client_backend_url.sh"

BACKEND_URL="$(aura_resolve_client_backend_url "$ENV_FILE")"
aura_require_release_backend_url "$BACKEND_URL"

echo "=========================================================="
echo "Building AURA AI Client for Desktop"
echo "   Target Backend: $BACKEND_URL"
echo "=========================================================="
echo "Before shipping, confirm TLS matches this hostname:"
echo "   $REPO_ROOT/deploy/scripts/verify-client-hostname.sh"
echo "=========================================================="

cd "$CLIENT_DIR"

echo "Cleaning Flutter build cache..."
flutter clean

echo "Resolving dependencies..."
flutter pub get

echo "Enabling Linux desktop support (no-op if already enabled)..."
flutter config --enable-linux-desktop

echo "Building production Linux desktop bundle..."
flutter build linux --release \
  --dart-define=BACKEND_URL="$BACKEND_URL"

if [ "$(uname)" == "Darwin" ]; then
  echo "Enabling macOS desktop support (no-op if already enabled)..."
  flutter config --enable-macos-desktop

  echo "Building production macOS desktop bundle..."
  flutter build macos --release \
    --dart-define=BACKEND_URL="$BACKEND_URL"
else
  echo "Skipping macOS build (current platform is not macOS)."
fi

echo "=========================================================="
echo "Desktop builds generated."
echo "   Linux bundle: aura_client/build/linux/x64/release/bundle/"
echo "   macOS bundle: aura_client/build/macos/Build/Products/Release/ (Darwin only)"
echo "   aura_hostd is NOT bundled -- build/install it separately (cd aura_hostd &&"
echo "   dart pub get && dart compile exe bin/aura_hostd.dart -o aura_hostd) and point the"
echo "   client's daemon executable-path setting at the resulting binary."
echo "=========================================================="
