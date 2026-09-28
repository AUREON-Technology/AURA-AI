/// Compile-time backend binding.
///
/// [backendUrl] defaults to the production deployment, not a local gateway: a build with no
/// `--dart-define=BACKEND_URL` points at `https://aureon-ai.tech`. Run against a local gateway
/// by injecting it (`--dart-define-from-file=config/dev.json`), not by relying on this default.
/// `test/backend_url_policy_test.dart` pins what the default must satisfy -- release-safe under
/// `BackendUrlPolicy` and accepted by its runtime allowlist -- rather than pinning one value.
/// Physical Android must inject production `https://$AURA_DOMAIN` via
/// `run_client_device.ps1` or `--dart-define-from-file=config/device.json`.
/// Release APKs, IPAs, and web bundles must inject `BACKEND_URL` via
/// `--dart-define` (see `deploy_client.sh` / `deploy_client.ps1`), which reads
/// `AURA_DOMAIN` from `deploy/.env`. Users may switch between allowlisted prod
/// and dev presets at runtime in Settings; see [backendUrlProvider].
class AppConfig {
  static const String backendUrl = String.fromEnvironment(
    'BACKEND_URL',
    defaultValue: 'https://aureon-ai.tech',
  );

  /// The Google OAuth client the desktop (Linux/Windows/macOS) loopback login uses, distinct
  /// from the "Web application"-type client hardcoded in `auth_provider.dart` (as
  /// `serverClientId`) and matching `aura_backend/config.py`'s `google_client_id`. Google
  /// restricts the loopback-IP redirect URI that flow needs
  /// (`core/auth/desktop_oauth_client.dart`, ADR-011 Unit 7) to native client types, so the web
  /// client can't be reused for it.
  ///
  /// An OAuth client id is not a secret -- it's a public identifier compiled into the app -- so
  /// it's hardcoded here the same way `serverClientId` is. It must stay equal to
  /// `aura_backend/config.py::google_desktop_client_id`, which
  /// `services/security.py::verify_identity_token` checks the ID token's `aud` against; a drift
  /// between the two rejects every desktop login. Overridable via
  /// `--dart-define=GOOGLE_DESKTOP_CLIENT_ID=...` for a build with its own registered client.
  ///
  /// **This id must name a client of Google's "Desktop app" type**, which is *public*: it
  /// authenticates with PKCE alone and needs no [googleDesktopClientSecret]. A "Web
  /// application"-type client is *confidential*, and its token endpoint answers a secret-less
  /// exchange with `invalid_request: client_secret is missing` -- the symptom once mistaken for
  /// something inherent to desktop login and worked around by shipping a secret instead.
  static const String googleDesktopClientId = String.fromEnvironment(
    'GOOGLE_DESKTOP_CLIENT_ID',
    defaultValue: '87789110323-pprobmjkf4f16rem4kacr2nqgi9ql1i0.apps.googleusercontent.com',
  );

  /// Normally empty, and normally should stay that way: the desktop flow authenticates with PKCE
  /// alone. RFC 8252 §8.5 is explicit that a native app is a *public* client -- there is no
  /// secret a shipped binary could actually keep -- and `signInWithGoogleLoopback` omits
  /// `client_secret` from the token exchange entirely when this is empty.
  ///
  /// It survives only as an escape hatch for a build whose [googleDesktopClientId] points at a
  /// *confidential* client (Google's "Web application" type), whose token endpoint does demand a
  /// secret. Inject it with `--dart-define=GOOGLE_DESKTOP_CLIENT_SECRET=...` in that case only.
  ///
  /// Never hardcode a value as this default. A real `GOCSPX-...` secret was committed here once
  /// and compiled into every desktop binary, and had to be rotated at Google;
  /// `aura_backend/tests/architecture/test_no_committed_client_secrets.py` is what now fails the
  /// build rather than trusting anyone to remember.
  static const String googleDesktopClientSecret = String.fromEnvironment(
    'GOOGLE_DESKTOP_CLIENT_SECRET',
    defaultValue: '',
  );
}
