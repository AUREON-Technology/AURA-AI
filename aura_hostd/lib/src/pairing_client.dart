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
///
/// [startPairing]/[pollPairingOnce] are split out from [pairWithCode] so a caller that cannot
/// block a request on the whole flow -- `control_server.dart`'s `/pair/start` and `/pair/status`,
/// answering a UI in a *different process* that needs the code back before approval happens, not
/// after -- can drive the same two backend calls without owning a sleep loop itself. `pairWithCode`
/// (what `aura_hostd auth pair`'s CLI path still uses) is now a thin wrapper composing both,
/// unchanged in behavior.
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

/// A just-started pairing session: the code to show the user, and everything a later
/// [pollPairingOnce] call needs to check on it.
class PairingSession {
  PairingSession({
    required this.pairingId,
    required this.userCode,
    required this.expiresIn,
    required this.intervalS,
  });

  final String pairingId;
  final String userCode;
  final int expiresIn;
  final int intervalS;
}

/// What one poll found: still waiting, approved (with the token), or the session is gone
/// (expired, or some status this client doesn't recognize -- treated the same way, since either
/// way there is nothing left to poll for).
sealed class PairingPollOutcome {}

class PairingPending implements PairingPollOutcome {}

class PairingApproved implements PairingPollOutcome {
  PairingApproved(this.result);
  final PairingResult result;
}

class PairingGone implements PairingPollOutcome {
  PairingGone(this.reason);
  final String reason;
}

/// This machine's hostname, or `null` when the platform will not say. Sent with a pairing start
/// so the approver and the device list can tell machines apart; never used as identity.
String? localDeviceName() {
  try {
    final name = Platform.localHostname.trim();
    return name.isEmpty ? null : name;
  } catch (_) {
    return null;
  }
}

/// Starts a pairing session against [backendUrl] and returns the code to show the user plus the
/// session details a caller needs to poll it later.
Future<PairingSession> startPairing(String backendUrl, {HttpClient? httpClient}) async {
  final client = httpClient ?? HttpClient();
  final ownsClient = httpClient == null;
  try {
    // The hostname is a label for the person approving the code -- "which machine is this?" --
    // and for the owner's device list later (FSA-004). The backend treats it as untrusted text.
    final start = await _postJson(client, backendUrl, '/api/v3/auth/desktop/pair/start', {
      'device_name': localDeviceName(),
    });
    return PairingSession(
      pairingId: start['pairing_id'] as String,
      userCode: start['user_code'] as String,
      expiresIn: (start['expires_in'] as num).toInt(),
      intervalS: (start['interval'] as num).toInt(),
    );
  } finally {
    if (ownsClient) client.close(force: true);
  }
}

/// Polls [backendUrl] once for [session]'s outcome. Callers that need to keep waiting on
/// [PairingPending] are responsible for their own delay between calls (matching
/// [PairingSession.intervalS]) and their own deadline (matching [PairingSession.expiresIn]) --
/// this does exactly one HTTP round trip, no looping, so it is safe to call from a context that
/// cannot block for the whole pairing window (an HTTP handler answering a separate poll of its
/// own, e.g. `control_server.dart`'s `/pair/status`).
Future<PairingPollOutcome> pollPairingOnce(
  String backendUrl,
  PairingSession session, {
  HttpClient? httpClient,
}) async {
  final client = httpClient ?? HttpClient();
  final ownsClient = httpClient == null;
  try {
    final poll = await _postJson(
      client,
      backendUrl,
      '/api/v3/auth/desktop/pair/poll',
      {'pairing_id': session.pairingId},
    );
    final status = poll['status'] as String?;
    if (status == 'approved') {
      return PairingApproved(PairingResult(
        accessToken: poll['access_token'] as String,
        deviceId: poll['device_id'] as String,
      ));
    }
    if (status == 'pending') {
      return PairingPending();
    }
    return PairingGone('pairing session ended with status "$status" before approval');
  } finally {
    if (ownsClient) client.close(force: true);
  }
}

/// Runs the start -> display-code -> poll -> return-token flow against [backendUrl] end to end,
/// blocking until it resolves -- what `aura_hostd auth pair`'s CLI path uses, since a terminal
/// command blocking until it's done is exactly the right UX there.
///
/// [onCode] is called once, as soon as the session starts, with the human-readable code to show
/// the user. Polls at the server-advised interval until the session is approved or its
/// `expires_in` elapses, at which point this throws [PairingFailedError] rather than polling
/// forever -- an abandoned pairing attempt must not leave this daemon spinning indefinitely.
Future<PairingResult> pairWithCode(
  String backendUrl, {
  required void Function(String userCode) onCode,
  HttpClient? httpClient,
}) async {
  final client = httpClient ?? HttpClient();
  final ownsClient = httpClient == null;
  try {
    final session = await startPairing(backendUrl, httpClient: client);
    onCode(session.userCode);

    final deadline = DateTime.now().add(Duration(seconds: session.expiresIn));
    while (DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(Duration(seconds: session.intervalS));
      final outcome = await pollPairingOnce(backendUrl, session, httpClient: client);
      switch (outcome) {
        case PairingApproved(result: final result):
          return result;
        case PairingGone(reason: final reason):
          throw PairingFailedError(reason);
        case PairingPending():
          continue;
      }
    }
    throw PairingFailedError(
      'pairing code expired after ${session.expiresIn}s without being approved -- run `aura_hostd '
      'auth pair` again',
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
