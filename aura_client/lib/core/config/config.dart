/// Compile-time backend binding.
///
/// Debug / IDE / `flutter run` default to the local gateway. Release APKs, IPAs,
/// and web bundles must inject `BACKEND_URL` via `--dart-define` (see
/// `deploy_client.sh` / `deploy_client.ps1`), which reads the canonical hostname
/// `AURA_DOMAIN` from `deploy/.env`. There is no runtime override.
class AppConfig {
  static const String backendUrl = String.fromEnvironment(
    'BACKEND_URL',
    defaultValue: 'http://localhost:8000',
  );
}
