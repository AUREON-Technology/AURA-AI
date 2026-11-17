import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

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

  /// `api.aureon-ai.tech`, not the apex: since 2026-11-14 the backend runs in a new Railway
  /// project and the apex is still bound to the old one, so it answers 404. A saved
  /// "production" choice resolves to this constant on every launch, so a build-time
  /// `BACKEND_URL` alone cannot move installed apps -- this value has to.
  static const String productionUrl = 'https://api.aureon-ai.tech';
  static const String developmentUrl = 'https://dev.aureon-ai.tech';

  static const Map<BackendEnvironment, String> presetUrls = {
    BackendEnvironment.production: productionUrl,
    BackendEnvironment.development: developmentUrl,
  };

  static const Map<String, BackendEnvironment> _urlToEnvironment = {
    productionUrl: BackendEnvironment.production,
    developmentUrl: BackendEnvironment.development,
  };

  static String urlFor(BackendEnvironment environment) => presetUrls[environment]!;

  /// Whether this is a store build. A field, not a getter on [kReleaseMode], so a test can
  /// exercise the release rules.
  @visibleForTesting
  static bool releaseBuild = kReleaseMode;

  /// The environments a user may pick. A release build offers production only:
  /// [developmentUrl] (`dev.aureon-ai.tech`) stopped resolving in DNS, and a user who picked
  /// it was signed out onto a server that does not exist, with the choice saved across
  /// launches. Debug and profile builds keep both for development.
  static List<BackendEnvironment> get selectableEnvironments =>
      releaseBuild ? const [BackendEnvironment.production] : BackendEnvironment.values;

  /// Whether a server picker is worth showing at all (more than one choice).
  static bool get offersEnvironmentChoice => selectableEnvironments.length > 1;

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
    if (!_secureStorageAvailable) return null;
    try {
      final raw = await auraSecureStorage.read(key: storageKey);
      return savedEnvironmentFrom(raw);
    } on Object {
      return null;
    }
  }

  /// Parses a stored choice. A choice this build no longer offers (a release build that
  /// inherited `development` from an earlier version) reads as none, so the app starts on
  /// production instead of on a server that does not resolve.
  @visibleForTesting
  static BackendEnvironment? savedEnvironmentFrom(String? raw) {
    final environment = switch (raw) {
      'production' => BackendEnvironment.production,
      'development' => BackendEnvironment.development,
      _ => null,
    };
    if (environment == null || !selectableEnvironments.contains(environment)) return null;
    return environment;
  }

  static Future<void> saveEnvironment(BackendEnvironment environment) async {
    if (!_secureStorageAvailable) return;
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
    return initialUrlSync();
  }

  static String initialUrlSync() {
    final compileTime = _normalizeUrl(AppConfig.backendUrl);
    if (isPresetUrl(compileTime)) return compileTime;
    if (isAllowed(compileTime)) return compileTime;
    // `flutter test` / `flutter run` default to localhost — do not map that to the
    // production preset or unit tests will take the release attestation path.
    if (!kReleaseMode && !BackendUrlPolicy.isSafeForRelease(compileTime)) {
      return compileTime;
    }
    return productionUrl;
  }

  static bool get _secureStorageAvailable {
    try {
      ServicesBinding.instance;
      return true;
    } on Object {
      return false;
    }
  }

  static String _normalizeUrl(String url) {
    final trimmed = url.trim();
    if (trimmed.endsWith('/')) return trimmed.substring(0, trimmed.length - 1);
    return trimmed;
  }
}
