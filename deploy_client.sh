#!/usr/bin/env bash
# AURA AI Flutter Client Production Build Script (Bash version)
# Run from anywhere. Resolves BACKEND_URL from AURA_DOMAIN in deploy/.env.
# Debug `flutter run` does not use this script; it defaults to http://localhost:8000.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT_DIR="$SCRIPT_DIR/aura_client"
ENV_FILE="$SCRIPT_DIR/deploy/.env"

# shellcheck source=deploy/scripts/client_backend_url.sh
source "$SCRIPT_DIR/deploy/scripts/client_backend_url.sh"

BACKEND_URL="$(aura_resolve_client_backend_url "$ENV_FILE")"
aura_require_release_backend_url "$BACKEND_URL"

echo "=========================================================="
echo "Building AURA AI Client for Production"
echo "   Target Backend: $BACKEND_URL"
echo "=========================================================="
echo "Before shipping, confirm TLS matches this hostname:"
echo "   $SCRIPT_DIR/deploy/scripts/verify-client-hostname.sh"
echo "=========================================================="

cd "$CLIENT_DIR"

echo "Cleaning Flutter build cache..."
flutter clean

echo "Resolving dependencies..."
flutter pub get

echo "Building production Android APK (Obfuscated & Split)..."
flutter build apk --release \
  --obfuscate \
  --split-debug-info=build/app/outputs/symbols \
  --split-per-abi \
  --dart-define=BACKEND_URL="$BACKEND_URL"

if [ "$(uname)" == "Darwin" ]; then
  echo "Building production iOS IPA (Obfuscated)..."
  flutter build ipa --release \
    --obfuscate \
    --split-debug-info=build/ios/outputs/symbols \
    --dart-define=BACKEND_URL="$BACKEND_URL"
else
  echo "Skipping iOS build (current platform is not macOS)."
fi

echo "Building production Web bundle..."
flutter build web --release \
  --web-renderer canvaskit \
  --no-source-maps \
  --dart-define=BACKEND_URL="$BACKEND_URL"

echo "=========================================================="
echo "Production builds generated."
echo "   Android APKs: aura_client/build/app/outputs/flutter-apk/"
echo "   Web files:    aura_client/build/web/"
echo "   Web CORS:     set ALLOWED_ORIGINS on the gateway to the origin that"
echo "                 hosts this bundle (plus https://\$AURA_DOMAIN)."
echo "=========================================================="
