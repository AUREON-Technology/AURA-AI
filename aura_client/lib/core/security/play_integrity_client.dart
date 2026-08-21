import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import 'hardware_attestation_client.dart';

/// Android Play Integrity Standard API via the official Play library.
///
/// The Kotlin side calls `IntegrityManager.requestIntegrityToken` and returns
/// the JWS string the backend sends to `playintegrity.decodeIntegrityToken`.
class PlayIntegrityClient implements HardwareAttestationClient {
  PlayIntegrityClient({MethodChannel? channel})
      : _channel = channel ?? const MethodChannel(kAttestationMethodChannel);

  final MethodChannel _channel;

  @override
  Future<String?> requestToken({String? nonce}) async {
    try {
      final token = await _channel.invokeMethod<String>(
        'requestPlayIntegrityToken',
        {'nonce': nonce ?? ''},
      );
      if (token == null || token.isEmpty) return null;
      return token;
    } on MissingPluginException {
      return null;
    } on PlatformException catch (error) {
      debugPrint('Play Integrity token request failed: ${error.code} ${error.message}');
      return null;
    }
  }
}
