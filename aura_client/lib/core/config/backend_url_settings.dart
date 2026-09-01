import 'package:aura_client/core/config/backend_url_policy.dart';
import 'package:aura_client/core/config/config.dart';
import 'package:aura_client/core/security/aura_secure_storage.dart';

/// Preset server environments the app may switch between at runtime.
enum BackendEnvironment {
  production,
  development,
}

/// Persistence and validation for the runtime prod/dev backend URL switch.
class BackendUrlSettings {
  BackendUrlSettings._();

  static const String storageKey = 'aura_backend_environment';

  static const String productionUrl = 'https://169.58.188.43.nip.io';
  static const String developmentUrl = 'https://dev.169.58.188.43.nip.io';

  static const Map<BackendEnvironment, String> presetUrls = {
    BackendEnvironment.production: productionUrl,
    BackendEnvironment.development: developmentUrl,
  };

  static const Map<String, BackendEnvironment> _urlToEnvironment = {
    productionUrl: BackendEnvironment.production,
    developmentUrl: BackendEnvironment.development,
  };

  static String urlFor(BackendEnvironment environment) => presetUrls[environment]!;

  static BackendEnvironment environmentForUrl(String url) {
    final normalized = _normalizeUrl(url);
    return _urlToEnvironment[normalized] ?? BackendEnvironment.production;
  }

  static String labelFor(BackendEnvironment environment) => switch (environment) {
        BackendEnvironment.production => 'Production',
        BackendEnvironment.development => 'Development',
      };

  static bool isPresetUrl(String url) => _urlToEnvironment.containsKey(_normalizeUrl(url));

  /// Validates [url] against release HTTPS rules and the known AURA host allowlist.
  static String? validationError(String url) =>
      BackendUrlPolicy.runtimeRejectionReason(url);

  static bool isAllowed(String url) => validationError(url) == null;

  static Future<BackendEnvironment?> loadSavedEnvironment() async {
    final raw = await auraSecureStorage.read(key: storageKey);
    return switch (raw) {
      'production' => BackendEnvironment.production,
      'development' => BackendEnvironment.development,
      _ => null,
    };
  }

  static Future<void> saveEnvironment(BackendEnvironment environment) async {
    final value = switch (environment) {
      BackendEnvironment.production => 'production',
      BackendEnvironment.development => 'development',
    };
    await auraSecureStorage.write(key: storageKey, value: value);
  }

  /// First launch uses compile-time [AppConfig.backendUrl] when it matches a
  /// preset; otherwise production. A saved environment overrides on later launches.
  static Future<String> resolveInitialUrl() async {
    final saved = await loadSavedEnvironment();
    if (saved != null) return urlFor(saved);

    final compileTime = _normalizeUrl(AppConfig.backendUrl);
    if (isPresetUrl(compileTime)) return compileTime;
    if (isAllowed(compileTime)) return compileTime;
    return productionUrl;
  }

  static String initialUrlSync() {
    final compileTime = _normalizeUrl(AppConfig.backendUrl);
    if (isPresetUrl(compileTime)) return compileTime;
    if (isAllowed(compileTime)) return compileTime;
    return productionUrl;
  }

  static String _normalizeUrl(String url) {
    final trimmed = url.trim();
    if (trimmed.endsWith('/')) return trimmed.substring(0, trimmed.length - 1);
    return trimmed;
  }
}
