import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'hardware_attestation_client.dart';

/// iOS App Attest via `DCAppAttestService`.
///
/// The Swift side returns a JSON envelope (`format`, `key_id`, `attestation`
/// and/or `assertion`, `challenge`) that the backend verifies cryptographically.
class AppAttestClient implements HardwareAttestationClient {
  AppAttestClient({MethodChannel? channel})
      : _channel = channel ?? const MethodChannel(kAttestationMethodChannel);

  final MethodChannel _channel;

  @override
  Future<String?> requestToken({String? nonce}) async {
    try {
      final token = await _channel.invokeMethod<String>(
        'requestAppAttestToken',
        {'nonce': nonce ?? ''},
      );
      if (token == null || token.isEmpty) return null;
      return token;
    } on MissingPluginException {
      return null;
    } on PlatformException catch (error) {
      debugPrint('App Attest token request failed: ${error.code} ${error.message}');
      return null;
    }
  }
}
