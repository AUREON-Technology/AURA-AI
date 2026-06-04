#!/bin/bash
# AURA AI Flutter Client Production Build Script (Bash version)
# Run from the `aura_client` directory.

BACKEND_URL="https://aura-ai-production.up.railway.app"

echo "=========================================================="
echo "🚀 Building AURA AI Client for Production"
echo "   Target Backend: $BACKEND_URL"
echo "=========================================================="

# 1. Clean build caches
echo "🧹 Cleaning Flutter build cache..."
flutter clean

# 2. Get dependencies
echo "📦 Resolving dependencies..."
flutter pub get

# 3. Compile Android release APK
# --obfuscate: hides symbol names
# --split-debug-info: extracts symbols to a separate folder to minify APK size
# --split-per-abi: builds separate APKs for arm64, armv7, and x86 to reduce download size
# --dart-define: injects BACKEND_URL compile-time variable
echo "🤖 Building production Android APK (Obfuscated & Split)..."
flutter build apk --release \
  --obfuscate \
  --split-debug-info=build/app/outputs/symbols \
  --split-per-abi \
  --dart-define=BACKEND_URL=$BACKEND_URL

# 4. Compile iOS release bundle (macOS Only)
if [ "$(uname)" == "Darwin" ]; then
  echo "🍎 Building production iOS IPA (Obfuscated)..."
  flutter build ipa --release \
    --obfuscate \
    --split-debug-info=build/ios/outputs/symbols \
    --dart-define=BACKEND_URL=$BACKEND_URL
else
  echo "⚠️ Skipping iOS build (current platform is not macOS)."
fi

# 5. Compile Web release build
echo "🌐 Building production Web bundle (CanvasKit)..."
flutter build web --release \
  --web-renderer canvaskit \
  --no-source-maps \
  --dart-define=BACKEND_URL=$BACKEND_URL

echo "=========================================================="
echo "✅ Production builds generated successfully!"
echo "   - Android APKs: aura_client/build/app/outputs/flutter-apk/"
echo "   - Web files:   aura_client/build/web/"
echo "=========================================================="
