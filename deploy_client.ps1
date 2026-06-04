# AURA AI Flutter Client Production Build Script
# Run this script from the `aura_client` directory.

$BACKEND_URL = "https://aura-ai-production-a31f.up.railway.app"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "🚀 Building AURA AI Client for Production" -ForegroundColor Cyan
Write-Host "   Target Backend: $BACKEND_URL" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Clean build caches
Write-Host "🧹 Cleaning Flutter build cache..." -ForegroundColor Yellow
flutter clean

# 2. Get dependencies
Write-Host "📦 Resolving dependencies..." -ForegroundColor Yellow
flutter pub get

# 3. Compile Android release APK
# --obfuscate: hides symbol names
# --split-debug-info: extracts symbols to a separate folder to minify APK size
# --split-per-abi: builds separate APKs for arm64, armv7, and x86 to reduce download size
# --dart-define: injects BACKEND_URL compile-time variable
Write-Host "🤖 Building production Android APK (Obfuscated & Split)..." -ForegroundColor Yellow
flutter build apk --release `
  --obfuscate `
  --split-debug-info=build/app/outputs/symbols `
  --split-per-abi `
  --dart-define=BACKEND_URL=$BACKEND_URL

# 4. Compile iOS release bundle
# Note: Requires macOS and Xcode environment
if ($IsMacOS) {
  Write-Host "🍎 Building production iOS IPA (Obfuscated)..." -ForegroundColor Yellow
  flutter build ipa --release `
    --obfuscate `
    --split-debug-info=build/ios/outputs/symbols `
    --dart-define=BACKEND_URL=$BACKEND_URL
} else {
  Write-Host "⚠️ Skipping iOS build (current platform is not macOS)." -ForegroundColor Gray
}

# 5. Compile Web release build
# --web-renderer canvaskit: ensures consistent rendering performance in modern browsers
# --no-source-maps: disables source map generation to protect proprietary code
Write-Host "🌐 Building production Web bundle (CanvasKit)..." -ForegroundColor Yellow
flutter build web --release `
  --web-renderer canvaskit `
  --no-source-maps `
  --dart-define=BACKEND_URL=$BACKEND_URL

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "✅ Production builds generated successfully!" -ForegroundColor Green
Write-Host "   - Android APKs: aura_client/build/app/outputs/flutter-apk/" -ForegroundColor Green
Write-Host "   - Web files:   aura_client/build/web/" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
