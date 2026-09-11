/// Compile-time backend binding.
///
/// Debug / IDE / `flutter run` default to the local gateway (`config/dev.json`).
/// Physical Android must inject production `https://$AURA_DOMAIN` via
/// `run_client_device.ps1` or `--dart-define-from-file=config/device.json`.
/// Release APKs, IPAs, and web bundles must inject `BACKEND_URL` via
/// `--dart-define` (see `deploy_client.sh` / `deploy_client.ps1`), which reads
/// `AURA_DOMAIN` from `deploy/.env`. Users may switch between allowlisted prod
/// and dev presets at runtime in Settings; see [backendUrlProvider].
class AppConfig {
  static const String backendUrl = String.fromEnvironment(
    'BACKEND_URL',
    defaultValue: 'http://localhost:8000',
  );

  /// A "Desktop app"-type Google OAuth client, distinct from the "Web application"-type client
  /// hardcoded in `auth_provider.dart` (as `serverClientId`) and matching
  /// `aura_backend/config.py`'s `google_client_id`. Google restricts the loopback-IP redirect
  /// URI Linux/Windows login
  /// needs (`core/auth/desktop_oauth_client.dart`, ADR-011 Unit 7) to that client type, so the
  /// existing web client can't be reused for it. Registered 2026-09-10 (Google Cloud project
  /// aura-ai-anas-gnedy) and hardcoded here the same way `serverClientId` is -- an OAuth client
  /// id is not a secret, it's a public identifier; the matching backend audience is
  /// `aura_backend/config.py::google_desktop_client_id`, hardcoded there the same way. Still
  /// overridable via `--dart-define=GOOGLE_DESKTOP_CLIENT_ID=...` at build time, matching how
  /// [backendUrl] is supplied, for a build that needs a different registered client.
  static const String googleDesktopClientId = String.fromEnvironment(
    'GOOGLE_DESKTOP_CLIENT_ID',
    defaultValue: '87789110323-pprobmjkf4f16rem4kacr2nqgi9ql1i0.apps.googleusercontent.com',
  );

  /// [googleDesktopClientId]'s matching secret. RFC 8252 and Google's own guidance say a
  /// "Desktop app" client is public and PKCE alone should be enough at the token endpoint, but
  /// the client actually registered for this app rejects a secret-less exchange with
  /// `invalid_request: client_secret is missing` (confirmed live -- see
  /// `desktop_oauth_client_io.dart::signInWithGoogleLoopback`'s doc comment). Unlike
  /// [googleDesktopClientId], this is NOT hardcoded here even though Google's own installed-app
  /// docs say it "cannot be treated as a secret" once shipped -- it still isn't committed to
  /// source control on principle (and this repo's own secret-scanning tooling would flag it).
  /// Injected only via `--dart-define=GOOGLE_DESKTOP_CLIENT_SECRET=...`
  /// (`scripts/deploy_client_desktop.sh`/`.ps1`, which resolve it from a `GOOGLE_DESKTOP_
  /// CLIENT_SECRET` entry in `deploy/.env` -- itself untracked, same as `AURA_DOMAIN`/
  /// `BACKEND_URL`). Empty by default: desktop login is refused with a clear error (see
  /// `signInWithGoogleLoopback`) until a build actually injects it.
  static const String googleDesktopClientSecret = String.fromEnvironment(
    'GOOGLE_DESKTOP_CLIENT_SECRET',
  );
}
