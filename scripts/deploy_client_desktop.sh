#!/usr/bin/env bash
# AURA AI Flutter Client Desktop Build Script (Bash version)
# Run from anywhere. Resolves BACKEND_URL from AURA_DOMAIN in deploy/.env, same as
# deploy_client.sh. Separate script (not a mode of deploy_client.sh) because the desktop
# targets need platform-specific system libraries the mobile/web build doesn't, and because
# `aura_hostd` -- the daemon the desktop app supervises (see docs/architecture/011-desktop-
# engineer-agent.md) -- is not bundled: a person still configures its executable path
# themselves, so this script does not attempt to build or embed it.
#
# Also resolves GOOGLE_DESKTOP_CLIENT_SECRET from deploy/.env (or the environment) and injects
# it via --dart-define, the one piece of desktop login (ADR-011 Unit 7) that must NEVER be
# hardcoded in source -- unlike the client id, deploy/.env itself is untracked (same as
# AURA_DOMAIN/BACKEND_URL), so this is the only place it should ever live on disk. Missing it
# is not a hard build failure (matching how an unconfigured GOOGLE_DESKTOP_CLIENT_ID was always
# handled): the build still succeeds, desktop login just refuses with a clear error until it's
# set, per signInWithGoogleLoopback's own runtime check.
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

GOOGLE_DESKTOP_CLIENT_SECRET="${GOOGLE_DESKTOP_CLIENT_SECRET:-}"
if [ -z "$GOOGLE_DESKTOP_CLIENT_SECRET" ]; then
  GOOGLE_DESKTOP_CLIENT_SECRET="$(aura_read_env_key GOOGLE_DESKTOP_CLIENT_SECRET "$ENV_FILE")"
fi

echo "=========================================================="
echo "Building AURA AI Client for Desktop"
echo "   Target Backend: $BACKEND_URL"
if [ -z "$GOOGLE_DESKTOP_CLIENT_SECRET" ]; then
  echo "   WARNING: GOOGLE_DESKTOP_CLIENT_SECRET is not set (deploy/.env or environment) --"
  echo "            this build's desktop login will refuse with a clear error until it is."
else
  echo "   Desktop OAuth client secret: configured"
fi
echo "=========================================================="
echo "Before shipping, confirm TLS matches this hostname:"
echo "   $REPO_ROOT/deploy/scripts/verify-client-hostname.sh"
echo "=========================================================="

cd "$CLIENT_DIR"

echo "Cleaning Flutter build cache..."
flutter clean

echo "Resolving dependencies..."
flutter pub get

DART_DEFINES=(--dart-define=BACKEND_URL="$BACKEND_URL")
if [ -n "$GOOGLE_DESKTOP_CLIENT_SECRET" ]; then
  DART_DEFINES+=(--dart-define=GOOGLE_DESKTOP_CLIENT_SECRET="$GOOGLE_DESKTOP_CLIENT_SECRET")
fi

echo "Enabling Linux desktop support (no-op if already enabled)..."
flutter config --enable-linux-desktop

echo "Building production Linux desktop bundle..."
flutter build linux --release "${DART_DEFINES[@]}"

if [ "$(uname)" == "Darwin" ]; then
  echo "Enabling macOS desktop support (no-op if already enabled)..."
  flutter config --enable-macos-desktop

  echo "Building production macOS desktop bundle..."
  flutter build macos --release "${DART_DEFINES[@]}"
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
