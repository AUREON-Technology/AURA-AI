import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/network/aura_api_client.dart';

/// What `POST /api/v3/push/devices` answers (ADR-060).
class PushRegistration {
  const PushRegistration({required this.registered, required this.delivering});

  final bool registered;

  /// Whether the server can actually send pushes. False means the token is stored and waits: the
  /// operator has not switched push on, so nothing will arrive yet.
  final bool delivering;
}

/// Registers this install's push token with the gateway so it can nudge the phone when an agent run
/// waits for approval. The notification carries no content; the app fetches what is held over the
/// authenticated API once opened.
///
/// Where the token comes from is a separate piece: obtaining an FCM token needs the
/// `firebase_messaging` plugin plus platform setup (Firebase project files, iOS APNs key), none of
/// which is in this app yet. Until then nothing calls [register]; this class is the contract the
/// plugin will call, tested against the real gateway shape.
class PushRegistrationClient {
  PushRegistrationClient(this._api);

  final AuraApiClient _api;

  Future<PushRegistration> register({
    required String deviceId,
    required String token,
    required String platform,
  }) async {
    final decoded = await _api.json(
      method: 'POST',
      path: '/api/v3/push/devices',
      body: {'device_id': deviceId, 'token': token, 'platform': platform},
    );
    return PushRegistration(
      registered: decoded['registered'] == true,
      delivering: decoded['delivering'] == true,
    );
  }

  /// Stop pushes to this install (sign-out). Idempotent on the server.
  Future<bool> unregister(String deviceId) async {
    final decoded = await _api.json(
      method: 'DELETE',
      path: '/api/v3/push/devices/${Uri.encodeComponent(deviceId)}',
    );
    return decoded['removed'] == true;
  }
}

final pushRegistrationClientProvider = Provider<PushRegistrationClient>(
  (ref) => PushRegistrationClient(ref.read(auraApiClientProvider)),
);
