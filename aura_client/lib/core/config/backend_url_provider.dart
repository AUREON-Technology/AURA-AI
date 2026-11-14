import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../auth/token_manager.dart';
import '../security/device_validation_service.dart';
import 'backend_url_settings.dart';

/// Runtime backend base URL (no trailing slash). Defaults to the compile-time
/// [AppConfig.backendUrl] preset when present, otherwise production.
class BackendUrlNotifier extends Notifier<String> {
  @override
  String build() {
    _loadPersistedUrl();
    return BackendUrlSettings.initialUrlSync();
  }

  Future<void> _loadPersistedUrl() async {
    try {
      final url = await BackendUrlSettings.resolveInitialUrl();
      if (state != url) state = url;
    } on Object {
      // Unit tests may initialize bindings without secure-storage plugins.
    }
  }

  BackendEnvironment get currentEnvironment =>
      BackendUrlSettings.environmentForUrl(state);

  /// Switches to a preset environment, clears JWTs, and drops cached attestation.
  ///
  /// Does not sign out: a signed-in caller must end the session on the *current* server
  /// first (`AuthNotifier.signOut` revokes the refresh token there and wipes this device's
  /// data), or the old server keeps a live refresh token and the local rows and outbox of one
  /// server are pushed to the other. `BackendEnvironmentPage` does that before calling this.
  Future<bool> setEnvironment(BackendEnvironment environment) async {
    final url = BackendUrlSettings.urlFor(environment);
    if (url == state) return false;
    if (!BackendUrlSettings.selectableEnvironments.contains(environment)) {
      throw StateError('${BackendUrlSettings.labelFor(environment)} is not available in this build.');
    }

    final error = BackendUrlSettings.validationError(url);
    if (error != null) {
      throw StateError(error);
    }

    await BackendUrlSettings.saveEnvironment(environment);
    state = url;
    DeviceValidationService.clearCache();
    await ref.read(tokenManagerProvider).clear();
    return true;
  }
}

final backendUrlProvider =
    NotifierProvider<BackendUrlNotifier, String>(BackendUrlNotifier.new);
