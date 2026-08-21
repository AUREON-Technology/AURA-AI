import 'package:aura_client/core/security/device_validation_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('DeviceValidationService', () {
    test('debug builds send an explicit aura_dev_attestation marker', () {
      final headers = DeviceValidationService.getAttestationHeaders(debugMode: true);
      expect(headers['X-Device-Attestation'], DeviceValidationService.devToken);
      expect(headers['X-Device-Platform'], isNotEmpty);

      final resolved = DeviceValidationService.resolve(debugMode: true);
      expect(resolved.isHardwareAttestation, isFalse);
      expect(resolved.isDebugBypass, isTrue);
    });

    test('release builds send the unsupported marker, not a Play Integrity mock', () {
      final headers = DeviceValidationService.getAttestationHeaders(debugMode: false);
      expect(headers['X-Device-Attestation'], DeviceValidationService.unsupportedMarker);
      expect(headers['X-Device-Attestation'], isNot(contains('play_integrity')));
      expect(headers['X-Device-Attestation'], isNot(contains('mock')));

      final resolved = DeviceValidationService.resolve(debugMode: false);
      expect(resolved.isHardwareAttestation, isFalse);
      expect(resolved.isDebugBypass, isFalse);
    });
  });
}
