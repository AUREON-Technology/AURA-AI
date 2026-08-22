# Platform support

What's actually shippable today, versus what the Flutter project scaffolding merely lets you
build.

## Store-distributed

**Android** — the only platform with a working release path end to end. Device attestation
(Google Play Integrity) is wired on both client and backend; see
[play-store-signing.md](play-store-signing.md) for the release-signing setup and
`.github/workflows/release-apk.yml` for the build. `aura_client/releases/` holds the current
sideload-installable build.

## Dev-only — do not distribute

**iOS, macOS, Windows, Linux, Web** all have real, maintained Flutter project scaffolding
(`aura_client/ios/`, `aura_client/macos/`, `aura_client/windows/`, `aura_client/linux/`,
`aura_client/web/` all build) and none of them have a working device-attestation path in a
release build:

- **iOS** has App Attest implemented on both client (`ios/Runner/AppAttestPlugin.swift`) and
  backend (`services/attestation/app_attest.py`) — it is the closest to store-ready, but has not
  been validated end to end against a real Apple Developer / App Store Connect setup the way
  Android's Play Integrity path has.
- **macOS, Windows, Linux, Web** have no attestation implementation at all.
  `DeviceValidationService._defaultClient` (`core/security/device_validation_service.dart`)
  returns `null` for every platform except `android`/`ios`, so a release build on any of these
  sends `aura_attestation_unsupported` — a marker the backend's production allowlist does not
  accept (`services/security.py`, gated behind `allow_mock_attestation`, which is refused
  outright in staging/production by `aura_core/config/settings.py`'s `is_production_like` check).
  **A release build for these platforms cannot authenticate against a production backend at
  all.** It is not a degraded experience; login itself fails.

No CI workflow currently builds release artifacts for any of these platforms (only
`client-ci.yml`'s `build-android` job and `release-apk.yml` produce distributable output), so
there is nothing today that would accidentally publish one — this is a statement of current
scope, not a gate against a real risk yet. If a build pipeline for any of these platforms is
added later, it must not skip this check.

**Why not just widen the backend's mock-attestation allowlist instead?** That flag
(`allow_mock_attestation`) is already correctly refused in staging/production regardless, so
adding these platforms' markers to it would not open a live security hole — but it also would not
grant real device integrity, only unverified acceptance. Not worth the added surface for zero
platforms actually shipping today. Revisit if one of these platforms gets a real attestation
implementation and an actual release pipeline.
