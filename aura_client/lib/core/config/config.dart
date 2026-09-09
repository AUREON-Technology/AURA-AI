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

  /// OAuth client ID for the Linux/Windows loopback sign-in flow
  /// (`DesktopGoogleOAuthService`). Must be a Google Cloud Console client of
  /// type "Desktop app" — Google only allows an unregistered loopback
  /// (`http://127.0.0.1:<port>`) redirect URI for that client type, not for
  /// the "Web application" client used by `serverClientId` in
  /// `auth_provider.dart`. Empty by default: unset means the desktop sign-in
  /// button surfaces a clear "not configured" error instead of attempting a
  /// flow that can only fail.
  static const String googleDesktopClientId = String.fromEnvironment(
    'GOOGLE_DESKTOP_CLIENT_ID',
    defaultValue: '',
  );
}
