import 'package:aura_client/core/security/device_validation_service.dart';
import 'package:aura_client/core/security/hardware_attestation_client.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeAttestationClient implements HardwareAttestationClient {
  _FakeAttestationClient({this.token});

  final String? token;

  @override
  Future<String?> requestToken({String? nonce}) async => token;
}

void main() {
  setUp(DeviceValidationService.clearCache);

  group('DeviceValidationService', () {
    test('debug builds send an explicit aura_dev_attestation marker', () async {
      final headers = await DeviceValidationService.getAttestationHeaders(
        debugMode: true,
        platform: 'android',
        client: _FakeAttestationClient(token: 'must-not-be-used'),
      );
      expect(headers['X-Device-Attestation'], DeviceValidationService.devToken);
      expect(headers['X-Device-Platform'], 'android');

      final resolved = await DeviceValidationService.resolve(
        debugMode: true,
        platform: 'android',
      );
      expect(resolved.isHardwareAttestation, isFalse);
      expect(resolved.isDebugBypass, isTrue);
    });

    test('release Android sends a Play Integrity token when the SDK produces one', () async {
      const jws = 'header.payload.signature';
      final headers = await DeviceValidationService.getAttestationHeaders(
        debugMode: false,
        platform: 'android',
        client: _FakeAttestationClient(token: jws),
      );
      expect(headers['X-Device-Attestation'], jws);
      expect(headers['X-Device-Attestation'], isNot(DeviceValidationService.unsupportedMarker));
      expect(headers['X-Device-Attestation'], isNot(contains('mock')));
      expect(headers['X-Device-Platform'], 'android');

      final resolved = await DeviceValidationService.resolve(
        debugMode: false,
        platform: 'android',
        client: _FakeAttestationClient(token: jws),
      );
      expect(resolved.isHardwareAttestation, isTrue);
      expect(resolved.isDebugBypass, isFalse);
    });

    test('release iOS sends an App Attest envelope when the SDK produces one', () async {
      const envelope =
          '{"format":"apple-appattest","key_id":"abc","attestation":"dGVzdA==","challenge":"n"}';
      final headers = await DeviceValidationService.getAttestationHeaders(
        debugMode: false,
        platform: 'ios',
        client: _FakeAttestationClient(token: envelope),
      );
      expect(headers['X-Device-Attestation'], envelope);
      expect(headers['X-Device-Platform'], 'ios');
      expect(headers['X-Device-Attestation'], isNot(DeviceValidationService.unsupportedMarker));
    });

    test('release sends the unsupported marker only when the SDK cannot produce a token', () async {
      final headers = await DeviceValidationService.getAttestationHeaders(
        debugMode: false,
        platform: 'android',
        client: _FakeAttestationClient(token: null),
      );
      expect(headers['X-Device-Attestation'], DeviceValidationService.unsupportedMarker);
      expect(headers['X-Device-Attestation'], isNot(contains('play_integrity')));
      expect(headers['X-Device-Attestation'], isNot(contains('mock')));

      final resolved = await DeviceValidationService.resolve(
        debugMode: false,
        platform: 'linux',
      );
      expect(resolved.token, DeviceValidationService.unsupportedMarker);
      expect(resolved.isHardwareAttestation, isFalse);
      expect(resolved.isDebugBypass, isFalse);
    });
  });
}
