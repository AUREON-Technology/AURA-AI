/// Daemon-side half of desktop pairing (ADR-011, `docs/architecture/011-desktop-engineer-agent.md`,
/// Unit 6). Trades a short human-readable code for a device-scoped access token by talking to the
/// three unauthenticated/self-authenticated endpoints `api/v2/auth.py` adds:
///
///   POST /api/v3/auth/desktop/pair/start  -> {pairing_id, user_code, expires_in, interval}
///   POST /api/v3/auth/desktop/pair/poll   -> {status: "pending"|"approved"|"expired", ...}
///
/// `pair/approve` is never called from here -- it requires an *already-authenticated* session
/// (mobile or web console), which is the whole point: this daemon has no identity yet, so it can
/// only start a session and poll it, exactly like OAuth's device authorization grant (RFC 8628)
/// this mirrors. `dart:io`'s `HttpClient` covers this without a new dependency, matching
/// `config.dart`'s stated bar for what's allowed to run with this daemon's access.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

/// Thrown when pairing cannot complete: the session expired before approval, or the backend
/// returned something this client can't make sense of.
class PairingFailedError implements Exception {
  PairingFailedError(this.message);

  final String message;

  @override
  String toString() => 'PairingFailedError: $message';
}

/// The outcome of a successful pairing: the token to hand to [ConfigStore.save] and the
/// `device_id` it's scoped to (shown to the user so they can find it later in `devices/revoke`).
class PairingResult {
  PairingResult({required this.accessToken, required this.deviceId});

  final String accessToken;
  final String deviceId;
}

/// Runs the start -> display-code -> poll -> return-token flow against [backendUrl].
///
/// [onCode] is called once, as soon as the session starts, with the human-readable code to show
/// the user (`aura_hostd auth pair`'s caller is expected to print it and tell them where to enter
/// it). Polls at the server-advised interval until the session is approved or its `expires_in`
/// elapses, at which point this throws [PairingFailedError] rather than polling forever -- an
/// abandoned pairing attempt must not leave this daemon spinning indefinitely.
Future<PairingResult> pairWithCode(
  String backendUrl, {
  required void Function(String userCode) onCode,
  HttpClient? httpClient,
}) async {
  final client = httpClient ?? HttpClient();
  final ownsClient = httpClient == null;
  try {
    final start = await _postJson(client, backendUrl, '/api/v3/auth/desktop/pair/start', {});
    final pairingId = start['pairing_id'] as String;
    final userCode = start['user_code'] as String;
    final expiresIn = (start['expires_in'] as num).toInt();
    final intervalS = (start['interval'] as num).toInt();

    onCode(userCode);

    final deadline = DateTime.now().add(Duration(seconds: expiresIn));
    while (DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(Duration(seconds: intervalS));
      final poll = await _postJson(
        client,
        backendUrl,
        '/api/v3/auth/desktop/pair/poll',
        {'pairing_id': pairingId},
      );
      final status = poll['status'] as String?;
      if (status == 'approved') {
        return PairingResult(
          accessToken: poll['access_token'] as String,
          deviceId: poll['device_id'] as String,
        );
      }
      if (status != 'pending') {
        // "expired" or anything unrecognized -- the session is gone either way, and a pending
        // status is the only one worth continuing to poll for.
        throw PairingFailedError('pairing session ended with status "$status" before approval');
      }
    }
    throw PairingFailedError(
      'pairing code expired after ${expiresIn}s without being approved -- run `aura_hostd auth '
      'pair` again',
    );
  } finally {
    if (ownsClient) client.close(force: true);
  }
}

Future<Map<String, dynamic>> _postJson(
  HttpClient client,
  String backendUrl,
  String path,
  Map<String, Object?> body,
) async {
  final uri = Uri.parse(backendUrl).replace(path: path);
  final request = await client.postUrl(uri);
  request.headers.contentType = ContentType.json;
  request.write(jsonEncode(body));
  final response = await request.close();
  final raw = await response.transform(utf8.decoder).join();
  if (response.statusCode < 200 || response.statusCode >= 300) {
    throw PairingFailedError('$path returned HTTP ${response.statusCode}: $raw');
  }
  final decoded = jsonDecode(raw);
  if (decoded is! Map<String, dynamic>) {
    throw PairingFailedError('$path returned a non-object response: $raw');
  }
  return decoded;
}
