# Play Store signing

Phase 8 uses the optional `signingConfig` from P0. This repo never generates a keystore.

Android application id (must match Play Integrity `packageName` and backend `android_package_name`):

`com.auraai.aura_client`

iOS bundle id (must match App Attest app ID suffix):

`com.auraai.auraClient`

## Upload keystore (operator)

Create a keystore **on a trusted machine**, not in CI and not in this tree:

```bash
keytool -genkey -v -keystore aura-upload.jks -keyalg RSA -keysize 2048 -validity 10000 -alias aura-upload
```

Keep the `.jks` and passwords in a password manager. Gitignores already cover `android/key.properties` and `*.jks`.

Local Gradle (gitignored `aura_client/android/key.properties`):

```
storeFile=../aura-upload.jks
storePassword=...
keyPassword=...
keyAlias=aura-upload
```

Or env: `AURA_RELEASE_STORE_FILE`, `AURA_RELEASE_STORE_PASSWORD`, `AURA_RELEASE_KEY_PASSWORD`, `AURA_RELEASE_KEY_ALIAS`.

If the store file is absent, `flutter run --release` still signs with the debug key so local smoke works. That APK cannot satisfy production Play Integrity.

## Play Console

1. Create the app with package name `com.auraai.aura_client`.
2. Enable Play App Signing. Upload the AAB signed with the **upload** key; Google holds the app signing key.
3. Link a Cloud project and enable the Play Integrity API. Grant a service account `playintegrity` scope; place the JSON at `deploy/secrets/firebase_credentials.json` (already mounted into the gateway).
4. App integrity / device integrity verdicts: production `verify_device_integrity` fails closed if `decodeIntegrityToken` is unavailable or the JWS is unsigned.

## CI

Workflow `.github/workflows/release-apk.yml`:

- `build-release-apk` — unsigned/debug-signed smoke APK (always).
- `build-signed-aab` — store AAB only when all four secrets exist. `AURA_RELEASE_STORE_FILE` is the keystore **base64**. Missing secrets skip the AAB steps; the workflow still succeeds.

```bash
base64 -w0 aura-upload.jks   # paste into the GitHub secret
```

## Build locally

```bash
cd aura_client
flutter build appbundle --release --dart-define=BACKEND_URL=https://$AURA_DOMAIN
```

Upload `build/app/outputs/bundle/release/app-release.aab` in Play Console.
