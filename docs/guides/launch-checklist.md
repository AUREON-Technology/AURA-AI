# Launch checklist

One page for a production cut. Tick these before calling the stack launch-ready.
The audit’s “launch checklist green” is this list, not a new product surface.

Package IDs that Play Integrity / App Attest verify:

- Android `applicationId`: `com.auraai.aura_client` (must match backend `android_package_name`)
- iOS bundle ID: `com.auraai.auraClient` (must match `AURA_IOS_BUNDLE_ID`)

## Operator prerequisites (not committed)

**Play Integrity.** Play Console app with package `com.auraai.aura_client`; Cloud project linked (this tree’s Firebase `project_number` is `87789110323`); Play Integrity API enabled; service account with Play Integrity; JSON at `deploy/secrets/firebase_credentials.json`.

**Apple App Attest.** App ID `com.auraai.auraClient` with the App Attest capability; `APPLE_TEAM_ID` in `deploy/.env`; Xcode team signing. Debug entitlements use the development App Attest environment; Release uses production.

**rclone offsite.** S3-compatible bucket; `BACKUP_S3_URI` in `.env`; credentials as `deploy/secrets/backup/rclone.conf` **or** `deploy/secrets/backup/backup_s3_access_key` + `backup_s3_secret_key` (the `backup/` subdirectory is all the sidecar mounts). Rebuild the backup image after pull (`docker compose build backup`). Leave `BACKUP_S3_URI` empty to skip offsite.

## Security flags

- [ ] `ALLOW_MOCK_ATTESTATION=false` in `deploy/.env` and prod compose (default is already false)
- [ ] `DEBUG_MODE=false` (compose hard-codes this; settings refuse boot if flipped in production)
- [ ] Play Integrity service-account JSON at `deploy/secrets/firebase_credentials.json` with the Play Integrity API enabled
- [ ] `APPLE_TEAM_ID` set for iOS App Attest verify
- [ ] Release clients send a real Play Integrity / App Attest token (not `aura_attestation_unsupported`, not `aura_dev_attestation`)

## Store artifact

- [ ] Upload keystore exists **off-repo** (`AURA_RELEASE_STORE_FILE` / gitignored `android/key.properties`)
- [ ] Signed AAB built (workflow `Release APK` job `build-signed-aab` when GitHub secrets are present, or local `flutter build appbundle --release`)
- [ ] Play App Signing enabled; upload key registered in Play Console
- [ ] See [Play Store signing](play-store-signing.md)

## Operations

- [ ] Alert test received (critical receiver: `AuraGatewayDown` / `AuraInsecureModeActive` / `AuraAllProvidersUnhealthy`)
- [ ] Restore drill dated (staging throwaway Postgres, not prod) — see the maintenance guide
- [ ] Offsite object listed (`rclone ls $BACKUP_S3_URI` shows a dump newer than 26 hours)
- [ ] `AuraBackupStale` / `AuraBackupOffsiteFailed` not firing
- [ ] v1 AI routes return **410**: `/api/chat/*`, `/api/rag/*`, `/api/memory/*`, `/api/telemetry/*` (v2 sync/auth/insights and automations/learning stay mounted)
- [ ] `GET /health/ready` is green (`healthy: true`)

v1 410 is implemented on the API cutover track. This checklist documents the expected end state; do not ship store traffic until that 410 is live **and** the Flutter client no longer calls those paths.
