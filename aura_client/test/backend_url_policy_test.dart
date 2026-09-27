import 'package:aura_client/core/config/backend_url_policy.dart';
import 'package:aura_client/core/config/backend_url_settings.dart';
import 'package:aura_client/core/config/config.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('BackendUrlPolicy', () {
    test('compile-time default is itself release-safe and runtime-allowlisted', () {
      // This once asserted the default was BackendUrlPolicy.localDevUrl, from when an
      // un-defined build meant `flutter run` against a local gateway. The default has pointed
      // at a real deployment for a long time (a VPS host, then https://aureon-ai.tech), so that
      // assertion only recorded history. What actually matters is what it protected: a build
      // that ships with no --dart-define=BACKEND_URL must still satisfy the same policy the
      // release gate applies to an injected one, and must be a preset the runtime allowlist
      // accepts -- otherwise BackendUrlProvider silently discards it at runtime.
      expect(BackendUrlPolicy.rejectionReason(AppConfig.backendUrl), isNull);
      expect(BackendUrlPolicy.isRuntimeAllowed(AppConfig.backendUrl), isTrue);
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
