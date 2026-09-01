import 'package:aura_client/core/config/backend_url_policy.dart';
import 'package:aura_client/core/config/backend_url_settings.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('BackendUrlSettings', () {
    test('maps presets to stable URLs', () {
      expect(
        BackendUrlSettings.urlFor(BackendEnvironment.production),
        BackendUrlSettings.productionUrl,
      );
      expect(
        BackendUrlSettings.urlFor(BackendEnvironment.development),
        BackendUrlSettings.developmentUrl,
      );
    });

    test('recognizes preset URLs', () {
      expect(BackendUrlSettings.isPresetUrl(BackendUrlSettings.productionUrl), isTrue);
      expect(
        BackendUrlSettings.isPresetUrl('${BackendUrlSettings.developmentUrl}/'),
        isTrue,
      );
      expect(BackendUrlSettings.isPresetUrl('https://unknown.example.org'), isFalse);
    });

    test('validates presets against runtime policy', () {
      expect(BackendUrlSettings.isAllowed(BackendUrlSettings.productionUrl), isTrue);
      expect(BackendUrlSettings.isAllowed(BackendUrlSettings.developmentUrl), isTrue);
      expect(BackendUrlSettings.validationError(BackendUrlPolicy.localDevUrl), isNotNull);
    });

    test('environmentForUrl falls back to production for unknown URLs', () {
      expect(
        BackendUrlSettings.environmentForUrl('https://unknown.example.org'),
        BackendEnvironment.production,
      );
      expect(
        BackendUrlSettings.environmentForUrl(BackendUrlSettings.developmentUrl),
        BackendEnvironment.development,
      );
    });
  });
}
