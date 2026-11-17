# Mobile app status

**As of 2026-10-28 (evening), `main` at `2337577`.** Covers the Flutter client `aura_client/` on Android and
iOS:
- talks to the backend's `/api/v3/*`;
- Riverpod for state, `go_router` for navigation;
- Drift for local data, synced through `/api/v3/sync`.

**Re-read against commit:** `b2e14c4` (2026-11-08) for every row dated 2026-11-07 or later (their live checks came first; the re-read confirmed that tree holds what they describe); older rows carry the commit they were checked at. Refresh this line whenever a row is re-verified (audit FSA-120).

Evidence scale and refresh steps: [README.md](README.md).

## Update 2026-11-16 (sign-in review, on `ebe7940`)

A review of how the app signs people in, then fixes for what could be fixed from the repo.
**TESTED locally** (Flutter 3.44.6: `flutter analyze` clean, `flutter test` 687 passed,
4 skipped). Not CI-run, not on a device.

Fixed:
- **No biometric enrolled meant a Google sign-in on every launch.** The saved session (refresh
  token in Keystore/Keychain) was resumed only after a fingerprint or face check, and
  `canCheckBiometrics` is true on hardware with nothing enrolled. The login page now resumes it
  directly when nothing is enrolled, and that includes Linux and Windows, whose device-bound
  desktop session was also never resumed. It does not resume right after the server ended
  the session (F-33's banner stays readable). `test/sign_in_session_test.dart`.
- **Google sign-in did not ask the backend.** `signInWithGoogle` let the user in and the
  `/auth/token` exchange ran on the first API call, so an account the server refuses got in on
  a dead session. `TokenManager.startSession` now runs the exchange at sign-in. A refusal
  (400/401/403) stops the sign-in with its own message (`SignInRefused`). An attestation
  rejection still lets the user in (C1: the app stays usable offline) and shows the "could not
  be verified" banner straight away. Network failures and 5xx let the user in, as before.
- **A sign-in as a different account kept renewing the old account's session.** A refresh
  token left on the device (signed out in the UI, kept for biometric unlock) survived a Google
  sign-in as another account. The local data was wiped for the new account, but `/refresh` kept
  issuing tokens for the old one. `startSession` now revokes the old token when the server
  answers, or forgets it when the server does not.
- **iOS Google sign-in config is wired, values pending.** `Info.plist` now reads `GIDClientID`
  and the callback URL scheme from `ios/Flutter/GoogleSignIn.xcconfig` (included by Debug and
  Release). The two values are empty until the owner creates the iOS OAuth client.
  `ios_release_config_test.dart` checks they match once set. Not built: no macOS here.

Still the owner's: the iOS OAuth client values, the same setup for macOS (plus its keychain
entitlement), the Firebase console check (SEC-08), and Sign in with Apple.

## Update 2026-11-14 (release readiness pass, on `b655421`)

A contract and release audit of the app against everything merged since 2026-10-28. Everything
below is **TESTED locally** (Flutter 3.44.6: `flutter analyze` clean, `flutter test` 673 passed,
3 skipped; backend pytest and legacy unittest, see the PR). None of it is CI-run, deployed or
checked on a device yet.

Fixed:
- **Account deletion (store requirement, Apple 5.1.1(v) / Google Play).** New
  `POST /api/v3/account/delete` (`aura_backend/api/account.py`) runs the operator erasure
  (`erase_account`, shared with `/admin/users/{subject}/forget`) on the caller; it needs
  `{"confirm": "DELETE"}` and refuses scoped daemon tokens and service keys. You → Account →
  Delete account confirms, calls it, then clears the device like sign-out (no final push).
  Erasure also records an erased-at time that `verify_access_token` checks against a token's
  `iat`, so another device's still-unexpired access token cannot sync the account back into
  existence. Known gap: that other device keeps its local copy until it signs in again, and
  signing back in with the same Google account starts a fresh account that its outbox can
  repopulate.
- **Plaintext DB key in Android backups.** The backup rules excluded `app_flutter/.aura_db_key`,
  but `getApplicationSupportDirectory()` is `filesDir`, so the real file `files/.aura_db_key`
  went into cloud backup and device transfer. Both rule files now name it
  (`test/android_backup_rules_test.dart`).
- **Answers withheld by the output guard stayed on screen and synced.** F-31's keep-partial path
  caught the post-stream `safety_violation` frame. The text is now replaced
  (`kChatWithheldText`) before it is queued; a request blocked before any token says so, neither
  with a Retry card (`chat_stream_stop_test.dart`).
- **Live voice died silently at token expiry.** The server re-checks the socket's token every
  60 s and closes with 4401. The socket now renews every 45 s with a token valid for at least
  2 minutes (`TokenManager.getAccessTokenValidFor`), and a server close ends the session with a
  message instead of leaving the screen "listening" (`live_voice_socket_test.dart`).
- **Images over ~3.7 MB got a 413** once vision is on: the flat 5 MB body cap sat under the
  10 MB per image `/capabilities` advertises. With vision enabled the chat routes now allow the
  base64 of `max_request_image_bytes` plus 1 MB (~17 MB with defaults, under nginx's 20 MB).
- **The dead `dev.aureon-ai.tech` preset is gone from release builds** (picker hidden; a saved
  `development` choice reads as production). In debug builds, switching server while signed in
  now asks, then signs out on the old server (revoke + wipe) before switching.
- **iOS:** deployment target 13.0 → 15.0 (the locked `firebase_core` 4.12.1 requires 15.0, so
  `pod install` would fail); `NSFaceIDUsageDescription` (biometric unlock),
  `NSPhotoLibraryUsageDescription` and `NSAppleMusicUsageDescription` (file_picker links both
  pickers; ITMS-90683) added (`test/ios_release_config_test.dart`).
- **Release workflow:** `--build-number=${{ github.run_number }}` (every build was versionCode 1,
  so Play would refuse the second upload); the `backend_url` input goes through `env`, not
  straight into the shell.
- **Release logs:** `debugPrint`, which Flutter does not strip, is silenced in release builds
  (it printed server response bodies).
- **`AuraDialog` buttons** ellipsize instead of overflowing on a 360 px phone.

Still open, needs the owner (cannot be done from the repo):
- **iOS sign-in:** the iOS OAuth client id (`GIDClientID` + reversed-client-id URL scheme), an
  Apple team id (`DEVELOPMENT_TEAM`), an App ID with App Attest, and a macOS build pipeline.
- **Sign in with Apple** (Apple 4.8: Google is the only sign-in). Needs an Apple Services ID and a
  backend token-exchange route; not started.
- **A privacy policy URL** (both stores; none exists in the repo), the Play Data Safety form, a
  web account-deletion URL for Play, App Privacy answers.
- **Android signing:** the `AURA_RELEASE_*` secrets (without them the AAB job skips) and the Play
  app-signing SHA-1 registered for Google sign-in.
- **Crash reporting:** none is wired; choosing a service is a privacy decision.

Accepted trade-offs from the review of this pass: with vision on, the chat routes buffer up
to ~17 MB before authentication rejects a request (nginx already allows 20 MB on every route on
the VPS; Railway has no nginx); a guarded turn's tool-call and citation metadata stay on the row
(only the answer text was guarded).

Still open in code (lower priority): `confirmation_invalid`, `budget_exceeded` and
`user_concurrency_limit` chat errors still render as "Connection Error" with a Retry card, and
a 429's Retry ignores `Retry-After` (P3); no app-level `PrivacyInfo.xcprivacy`; Play Integrity
uses the classic API's daily quota.

## Summary

Every mobile feature is built and most of the core plumbing is tested, but nothing is proven. No
real-device report exists for Android or iOS, and the UX transformation status lists Phase 12
"Mobile UX" as not started.

Client CI has runners again:
- `flutter analyze`: clean;
- tests: 381 passed, 3 skipped;
- the Android CI build fails because the runner runs out of disk.

iOS has no build pipeline. The APK committed to the repo is stale and points at the old
`nip.io` host.

The production backend the app talks to (`aureon-ai.tech`) runs on Railway at current `main`. Its
database was migrated to `0016` on 2026-10-30 (CB-43 resolved), and `/health/ready` is healthy, so
the schema no longer blocks sign-in or sync. No real account has probed either since.

Merged since `dc87beb`, not yet CI-run (CB-44): F-04 client half (approve by `approval_id`,
`a0c1b38`); sync F-07/F-11/F-12/F-13 (`98143ff`, `31608f9`, `19c64f9`, `c5b1924`); the Android
CI disk fix (`5ec27c9`); the failure classifier on every `AsyncValue.error` (#143). The
capability rows below that cite those findings as open describe `dc87beb`; treat them as BUILT
(fixed, unverified) until a Client CI run is green.

## Capabilities

| Area | Status | Evidence |
| --- | --- | --- |
| Chat | TESTED; defects open | `chat_cutover_test.dart`, `sse_frame_parser_test.dart` are green. F-31 fixed on 2026-10-30 (Stop button, 45s idle timeout, partial answer kept and marked interrupted; `chat_stream_stop_test.dart`, run locally — CI has no runners, CB-44). F-04: an approval replays `userMessages.last`. |
| Voice | TESTED (protocol); BUILT (live, on device) | `live_voice_protocol_test.dart`, `voice_client_test.dart`. GAP-020: speech-to-text is English-only. Live voice on a real device is roadmap M12. |
| Knowledge / documents | TESTED (client) | `knowledge_center_test.dart`. Backend F-14: deleted docs stay searchable. |
| Memory | TESTED, but broken against the server | F-07: the client id (`getDeterministicId`, `memory_repository.dart:171`) does not match the server's. F-13: `sync_payloads.dart` sends `content_enc`. |
| Tasks / calendar / finance / health | BUILT (finance tested) | F-11: health "today" rows collide across devices. F-18 / CB-28 built 2026-10-31 (ADR-032, Option A; TESTED locally, not deployed or CI-run): the agent now writes the same synced rows, so its tasks appear after a sync and it sees the user's own. F-32 fixed on 2026-10-30 for the task card and the health card (`life_hub_contrast_test.dart`, at least 4.5:1 under `AuraThemeV2.light`; `legacy_theme_color_ratchet_test.dart` stops legacy `AuraTheme` colours under `lib/features/` from growing). About 177 legacy uses remain, so other Life Hub screens are not yet checked on the light theme. F-17: fixed in code, untested on a device: insights and the daily briefing compute "today" in the IANA zone the app sends as `X-Client-Timezone` (stored as `users.timezone`, migration 0018; UTC when absent). | |
| Account data key and multi-device encryption (ADR-032) | TESTED locally (`test/data_key_crypto_test.dart`, 16 tests; full suite 529 passed, 3 skipped; `flutter analyze` clean); not deployed, not CI-run | The app fetches the account data key (`GET /api/v3/keys/data`) during a sync and moves local data off the per-device key: re-encrypts every row, queues the rows for upload through the outbox, and then retires the device key. The move resumes if the app is killed partway, and the app keeps working on the device key while offline. Every device of an account now shares one key, so another device's rows no longer trigger the `beforeOpen` key-loss wipe. **The server holds this key: the synced fields are not zero-knowledge any more.** |
| Offline sync (data only; no model on device) | TESTED (outbox); broken end to end | Outbox tests are green. F-11: pull is skipped when push fails. F-12: the cursor comes from the app clock. F-13: memory rows are rejected forever. |
| Auth and sign-out | TESTED | 2026-11-16 (TESTED locally): a saved session is resumed without a prompt when no biometric is enrolled; the backend exchange runs at Google sign-in and a refusal stops it; a sign-in as another account revokes the old refresh token (`test/sign_in_session_test.dart`). `5153394`: revoke and wipe on sign-out. F-33 and F-34 fixed on 2026-10-30 (TESTED locally, not proven): a 401 followed by a refused refresh ends the session and the router shows login, keeping local data (`test/ux/session_expiry_test.dart`); sign-out asks first. Switching backend (`setEnvironment()`) clears tokens without revoking them or wiping data. |
| Device attestation | TESTED (unit) | Play Integrity (`IntegrityAttestationPlugin.kt`) and App Attest (`AppAttestPlugin.swift`), with backend tests green. F-09: until 2026-11-16 any account could skip attestation through `/desktop/login`; that route is now for operators only, like `/web/token` (owner decision; TESTED locally, not deployed). |
| Account deletion | TESTED locally (2026-11-14) | `POST /api/v3/account/delete` + You → Account → Delete account; `test/account_deletion_test.dart`, `tests/unit/test_account_self_deletion.py`. Not deployed. |
| Push notifications | OPEN | No `firebase_messaging`; a failed `Firebase.initializeApp()` is ignored (`main.dart:26`); iOS has no `GoogleService-Info.plist`; in-app notification centre only |
| Accessibility / i18n | BUILT; TESTED locally (F-36 foundation, 2026-10-30) | `design_system_contrast_test.dart`, `test/ux/`. F-36: every `IconButton` under `lib/` now has a tooltip, pinned by `test/ux/icon_button_tooltip_test.dart` (Send already had one; the 18 real gaps were in tasks, calendar, finance, memory bank, hostd page, live voice, analytics, GitHub tab, plus the chat header menu button). Strings for chat, sign-in, You/settings and the design-system components moved to `app_en.arb` (34 → 203 keys) and are read via `context.l10n` (`lib/l10n/l10n.dart`, English fallback when no delegate is present). Other features still hold 209 hard-coded `Text('...')` literals, held by `test/ux/hardcoded_text_ratchet_test.dart`. RTL: layout code uses directional APIs (`test/ux/rtl_layout_test.dart` guard), and the chat composer, empty chat, You and sign-in screens render under `TextDirection.rtl` at 360 px with no exception. No Arabic locale yet. Screen-reader output and Arabic glyph shaping have not been checked on a device. |
| Android release (APK/AAB) | BUILT; not currently built in CI | See below |
| iOS release (IPA) | OPEN | No CI, no team ID, Google sign-in config missing (below) |

## Build, release and verification

- **Client CI:**
  - `analyze-and-test`: green (381 passed).
  - `build-android`: fails with "No space left on device" at Gradle `mergeJavaRes` (runs #142,
    #143).
  - `build-windows`: red, but non-gating.
- **`release-apk.yml`:** run by hand, defaulting to `https://aureon-ai.tech`. Pinned to Flutter
  3.44.6 on 2026-10-30, the same as Client CI (it floated on `stable`; GAP-044); not run since. It has succeeded once, on 2026-08-19, and that artifact has expired.
  Without the `AURA_RELEASE_*` secrets the AAB falls back to debug signing.
- **Committed APK: removed from the index on 2026-10-30** (F-29 / CB-13 / F-37). It was 72 MB,
  predated `5153394` and had `https://169.58.188.43.nip.io` baked in. The three 5 MB
  `scratch_test_*.tmp` files went with it. Both stay in git history (shrinking clones needs a
  history rewrite, an owner decision). Install builds come from the `release-apk.yml` artifact;
  `aura_client/releases/` and `*.apk` are now ignored, and `repo-hygiene.yml` fails a PR that
  tracks one again.
- **Backend URL:** fixed at build time, plus a runtime switch between allowlisted presets
  (`aureon-ai.tech`, `dev.aureon-ai.tech`). `dev.aureon-ai.tech` no longer resolves in DNS.
- **iOS:** no CI job and no IPA workflow. `scripts/deploy_client.sh` builds an IPA on macOS only.
  There is no `DEVELOPMENT_TEAM`. Bundle id `com.auraai.auraClient`, iOS 15 (raised from 13 on 2026-11-14 for `firebase_core`).

## Open issues (most severe first)

1. **F-11, F-13, F-12 (High).** Sync stalls on one bad row; the memory payload key and id are
   wrong; the cursor can skip rows.
2. **F-04 (High).** Approving a tool call replays the wrong message.
3. **F-07 (High).** Memory forget and chat delete never reach the server.
4. **F-31 (High).** Fixed on 2026-10-30 (TESTED locally). It is not proven until someone runs it against the live keepalive stream.
5. **F-32 (High).** Light-theme contrast: the task and health cards are fixed. The rest is held by a ratchet test.
6. ~~**F-09 (Medium).** Attestation bypass.~~ Closed in code on 2026-11-16: desktop sign-in is for operators only (ADR-011 amendment). Not deployed or CI-run.
7. **F-36 (Medium).** Foundation built on 2026-10-30 and TESTED locally (analyze clean, 513 passed /
   3 skipped). It is not proven. Still open: a screen-reader pass on a real device (R4.6 acceptance),
   extracting the remaining features' strings (ratchet at 209), and an Arabic locale
   (system-wide Arabic plan with owner decisions, 2026-11-17: `docs/plans/arabic-language_plan.md`).
   **F-33 and F-34** fixed on 2026-10-30, TESTED locally
   only (CI has no runners, CB-44), not proven on a device:
   - F-33: an expired session ends and routes to login; the Memory page shows its error state
     and failed or unconfirmed deletes; screens render failures through `AuraFailure`, pinned by
     `test/ux/no_raw_error_text_test.dart`.
   - F-34: settings sub-pages are pushed (back returns to You); task and conversation deletes
     have Undo (the delete is deferred until the snackbar closes, so nothing reaches the sync
     outbox or the server for an undone delete); sign-out asks for confirmation.
8. **Release hygiene.** GAP-044, the `build-android` disk space, the stale tracked APK.
9. **iOS, new (code-read only).**
   - `GIDClientID` and the Google URL scheme are wired from `ios/Flutter/GoogleSignIn.xcconfig`
     (2026-11-16), but the values are empty until the owner creates the iOS OAuth client. Until
     then Google sign-in on iOS fails.
   - ~~`NSFaceIDUsageDescription` is missing~~: added on 2026-11-14.

**Fixed on PR #185 (TESTED locally: analyze clean, 430 passed / 3 skipped; not merged):**
F-11 (pull runs even when push fails; outbox records attempts and last error, Drift schema 13),
F-12 (server-side cursor), F-13 (memories sent as `fact_enc` under a UUID, pinned by
`test/fixtures/sync_payload_contract.json`), F-07 (memory forget and chat delete reach the
server), F-04 (approvals carry the server's `approval_id`; older builds cannot confirm held calls
until updated), and the `build-android` disk fix (Client CI run 145 green).

**Fixed since 2026-10-26:** F-05 sign-out (`5153394`). Backend fixes F-02 and F-15, now live on
Railway.

**PR #143** (classify errors on every `AsyncValue.error`): open since 2026-09-28. It merges
cleanly, but its CI runs died during the old runner outage, and it has not been re-run since.

## Changed since 2026-10-15

- `5153394`: sign-out revokes the refresh token and wipes the Drift user tables, the outbox and
  the cursor. It is the only `aura_client` commit in that window.
- Backend changes that matter to mobile: `2d42388` (role re-resolved on refresh) and `19244e6`
  (memory decay).

## Next steps

1. Free disk in `client-ci.yml` `build-android` so Client CI is fully green. Then enable branch
   protection (CB-02).
2. Re-run CI on PR #143 and merge it. Check F-33 session-expiry routing on a device against a
   revoked refresh token.
3. Fix the memory id and the `fact_enc` key together, then make sync push per-mutation and let
   pull run even when push fails (F-13, F-11). Then F-07.
4. The client half of F-04. F-31's Stop button and idle timeout are done.
5. Pin Flutter in `release-apk.yml` (GAP-044) and run it. Delete the stale APK and `.tmp` files.
   Make `setEnvironment()` revoke and wipe.
6. **iOS:** fill in `ios/Flutter/GoogleSignIn.xcconfig` (the iOS OAuth client), a team ID, and
   a macOS CI job that produces the first IPA.
7. Commit a real-device report for Android and iOS: sign-in with attestation, chat, live voice,
   and two-device sync. Do the Firebase console check (SEC-08) at the same time.

## Unverified

- No device or emulator was run. Rendering, screen readers, contrast and biometrics are inferred
  from code.
- The iOS sign-in failure is inferred from code.
- Play Integrity and App Attest end to end against production.
- The Firebase auth providers (SEC-08).
