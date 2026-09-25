import 'package:aura_client/core/config/backend_url_policy.dart';
import 'package:aura_client/core/config/backend_url_settings.dart';
import 'package:aura_client/core/config/config.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('BackendUrlPolicy', () {
    test('compile-time default is the local gateway', () {
      expect(AppConfig.backendUrl, BackendUrlPolicy.localDevUrl);
    });

    test('accepts an https hostname', () {
      expect(
        BackendUrlPolicy.isSafeForRelease('https://aura.example.org'),
        isTrue,
      );
      expect(
        BackendUrlPolicy.isSafeForRelease('https://aureon-ai.tech'),
        isTrue,
      );
      expect(
        BackendUrlPolicy.rejectionReason(
          'https://staging.placeholder.invalid',
        ),
        isNull,
      );
    });

    test('rejects localhost and cleartext', () {
      expect(
        BackendUrlPolicy.rejectionReason(BackendUrlPolicy.localDevUrl),
        contains('https'),
      );
      expect(
        BackendUrlPolicy.rejectionReason('https://localhost:8000'),
        contains('localhost'),
      );
    });

    test('rejects a raw IP', () {
      expect(
        BackendUrlPolicy.rejectionReason('https://169.58.188.43'),
        contains('hostname'),
      );
    });

    test('rejects empty or relative values', () {
      expect(BackendUrlPolicy.rejectionReason(''), isNotNull);
      expect(BackendUrlPolicy.rejectionReason('aura.example.org'), isNotNull);
    });

    test('runtime allowlist accepts known AURA presets', () {
      expect(
        BackendUrlPolicy.isRuntimeAllowed(BackendUrlSettings.productionUrl),
        isTrue,
      );
      expect(
        BackendUrlPolicy.isRuntimeAllowed(BackendUrlSettings.developmentUrl),
        isTrue,
      );
    });

    test('runtime allowlist rejects unknown hosts even when https', () {
      expect(
        BackendUrlPolicy.runtimeRejectionReason('https://evil.example.org'),
        contains('allowlist'),
      );
    });
  });
}
