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
  /// existing web client can't be reused for it. Empty by default: desktop login is refused
  /// with a clear error (see `signInWithGoogleLoopback`) until this is injected via
  /// `--dart-define=GOOGLE_DESKTOP_CLIENT_ID=...` at build time, matching how [backendUrl] is
  /// supplied, and the corresponding `AURA_GOOGLE_DESKTOP_CLIENT_ID` the backend needs set too
  /// (`aura_backend/config.py::google_desktop_client_id`).
  static const String googleDesktopClientId = String.fromEnvironment(
    'GOOGLE_DESKTOP_CLIENT_ID',
    defaultValue: '',
  );
}
