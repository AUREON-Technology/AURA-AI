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
}
