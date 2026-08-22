import 'dart:convert';

import 'package:aura_client/core/security/device_validation_service.dart';
import 'package:aura_client/core/security/hardware_attestation_client.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _FakeAttestationClient implements HardwareAttestationClient {
  _FakeAttestationClient({this.token});

  final String? token;
  String? lastNonce;

  @override
  Future<String?> requestToken({String? nonce}) async {
    lastNonce = nonce;
    return token;
  }
}

/// A MockClient that answers POST /api/v3/auth/nonce instantly, so
/// release-mode tests stay hermetic instead of hitting the real network.
http.Client _fakeNonceClient({String nonce = 'server-nonce-1'}) {
  return MockClient((request) async {
    if (request.url.path.endsWith('/auth/nonce')) {
      return http.Response(jsonEncode({'nonce': nonce}), 200);
    }
    return http.Response('not found', 404);
  });
}

void main() {
  setUp(DeviceValidationService.clearCache);

  group('DeviceValidationService', () {
    test('debug builds send the mock marker even against a production URL', () async {
      const jws = 'header.payload.signature';
      final headers = await DeviceValidationService.getAttestationHeaders(
        debugMode: true,
        platform: 'android',
        backendUrl: 'https://169.58.188.43.nip.io',
        client: _FakeAttestationClient(token: jws),
      );
      expect(headers['X-Device-Attestation'], DeviceValidationService.devToken);
    });

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
        httpClient: _fakeNonceClient(),
      );
      expect(headers['X-Device-Attestation'], jws);
      expect(headers['X-Device-Attestation'], isNot(DeviceValidationService.unsupportedMarker));
      expect(headers['X-Device-Attestation'], isNot(contains('mock')));
      expect(headers['X-Device-Platform'], 'android');
      expect(headers['X-Device-Attestation-Nonce'], 'server-nonce-1');

      final fakeClient = _FakeAttestationClient(token: jws);
      final resolved = await DeviceValidationService.resolve(
        debugMode: false,
        platform: 'android',
        client: fakeClient,
        httpClient: _fakeNonceClient(),
      );
      expect(resolved.isHardwareAttestation, isTrue);
      expect(resolved.isDebugBypass, isFalse);
      expect(resolved.nonce, 'server-nonce-1');
      // The server-issued nonce must actually be what gets bound into the
      // hardware SDK call, not just returned as a header (H1).
      expect(fakeClient.lastNonce, 'server-nonce-1');
    });

    test('release Android falls back to a local nonce when the nonce endpoint is unreachable', () async {
      const jws = 'header.payload.signature';
      final failingClient = MockClient((request) async => http.Response('boom', 500));
      final fakeAttestation = _FakeAttestationClient(token: jws);
      final resolved = await DeviceValidationService.resolve(
        debugMode: false,
        platform: 'android',
        client: fakeAttestation,
        httpClient: failingClient,
      );
      expect(resolved.isHardwareAttestation, isTrue);
      expect(resolved.nonce, isNotNull);
      expect(resolved.nonce, isNotEmpty);
      expect(fakeAttestation.lastNonce, resolved.nonce);
    });

    test('release iOS sends an App Attest envelope when the SDK produces one', () async {
      const envelope =
          '{"format":"apple-appattest","key_id":"abc","attestation":"dGVzdA==","challenge":"n"}';
      final headers = await DeviceValidationService.getAttestationHeaders(
        debugMode: false,
        platform: 'ios',
        client: _FakeAttestationClient(token: envelope),
        httpClient: _fakeNonceClient(),
      );
      expect(headers['X-Device-Attestation'], envelope);
      expect(headers['X-Device-Platform'], 'ios');
      expect(headers['X-Device-Attestation'], isNot(DeviceValidationService.unsupportedMarker));
      expect(headers['X-Device-Attestation-Nonce'], 'server-nonce-1');
    });

    test('release sends the unsupported marker only when the SDK cannot produce a token', () async {
      final headers = await DeviceValidationService.getAttestationHeaders(
        debugMode: false,
        platform: 'android',
        client: _FakeAttestationClient(token: null),
        httpClient: _fakeNonceClient(),
      );
      expect(headers['X-Device-Attestation'], DeviceValidationService.unsupportedMarker);
      expect(headers['X-Device-Attestation'], isNot(contains('play_integrity')));
      expect(headers['X-Device-Attestation'], isNot(contains('mock')));
      // No hardware token means no nonce header either -- nothing to
      // correlate server-side.
      expect(headers.containsKey('X-Device-Attestation-Nonce'), isFalse);

      final resolved = await DeviceValidationService.resolve(
        debugMode: false,
        platform: 'linux',
      );
      expect(resolved.token, DeviceValidationService.unsupportedMarker);
      expect(resolved.isHardwareAttestation, isFalse);
      expect(resolved.isDebugBypass, isFalse);
      expect(resolved.nonce, isNull);
    });
  });
}
