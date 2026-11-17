import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../../../auth/token_manager.dart';
import '../../../config/backend_url_provider.dart';
import '../../../security/device_validation_service.dart';
import 'live_voice_protocol.dart';

/// Fetches the attestation headers sent inside `auth` / `reauth` frames.
typedef LiveAttestationFetcher = Future<Map<String, String>> Function(String backendUrl);

Future<Map<String, String>> _defaultAttestation(String backendUrl) =>
    DeviceValidationService.getAttestationHeaders(backendUrl: backendUrl);

/// WebSocket client for `/api/v3/voice/live`.
///
/// Auth mirrors [VoiceClient]: bearer token + attestation inside the `auth` /
/// `reauth` frames (WS clients cannot always set HTTP Authorization).
class LiveVoiceSocket {
  LiveVoiceSocket(
    this._ref, {
    WebSocketChannel Function(Uri uri)? connect,
    LiveAttestationFetcher? attestation,
    this.reauthInterval = defaultReauthInterval,
  })  : _connect = connect,
        _attestation = attestation ?? _defaultAttestation;

  /// The server re-checks the socket's token every 60 s (`ws_auth.DEFAULT_RECHECK_S`) and
  /// closes with [closeUnauthorized] once it has expired. A `reauth` after each turn was the
  /// only renewal, so a session longer than the 15-minute token without a turn finishing at the
  /// right moment -- or one opened on a token with under ~90 s left -- was cut off. Renewing on
  /// a timer shorter than the re-check, with a token good for longer than it, keeps it open.
  static const defaultReauthInterval = Duration(seconds: 45);

  /// A `reauth` carries a token valid for at least this long ([TokenManager.getAccessTokenValidFor]).
  static const reauthMinValidity = Duration(minutes: 2);

  /// The server's close code for a credential that no longer authenticates.
  static const closeUnauthorized = 4401;

  /// The deployment has live voice switched off, or its engines are absent
  /// (`voice_live.py` `_CLOSE_UNAVAILABLE`). Retrying cannot help.
  static const closeUnavailable = 4503;

  /// This account already holds its live-voice session(s), or the per-user rate limit refused
  /// the socket (`_CLOSE_TOO_MANY`). The server's session lock outlives a crashed worker for
  /// up to its TTL, so the message says to wait rather than to retry at once.
  static const closeTooMany = 4429;

  /// The server rejected a frame: no `start` in time, no `session_id`, an unsupported
  /// language (`_CLOSE_BAD_REQUEST`).
  static const closeBadRequest = 4400;

  /// Maps the server's close code to the error frame the screen shows. Each code has its own
  /// message: they ask the user for different things (sign in, wait, give up, report it).
  static LiveErrorFrame errorForCloseCode(int? code) {
    return switch (code) {
      closeUnauthorized => const LiveErrorFrame(
          error: 'session_expired',
          message: 'Live voice ended because your sign-in needs renewing. Start it again.',
        ),
      closeUnavailable => const LiveErrorFrame(
          error: 'feature_unavailable',
          message: 'Live voice is not available on this server.',
        ),
      closeTooMany => const LiveErrorFrame(
          error: 'too_many_sessions',
          message: 'Live voice is already open on another device, or you have started it too '
              'often. Close it elsewhere or wait a few minutes, then try again.',
        ),
      closeBadRequest => const LiveErrorFrame(
          error: 'bad_request',
          message: 'The server could not start live voice with these settings.',
        ),
      _ => const LiveErrorFrame(
          error: 'socket_closed',
          message: 'Live voice was disconnected. Start it again.',
        ),
    };
  }

  /// Every error `error` value that means the socket is gone.
  static const terminalErrors = {
    'session_expired',
    'feature_unavailable',
    'too_many_sessions',
    'bad_request',
    'socket_closed',
  };

  final Ref _ref;
  final WebSocketChannel Function(Uri uri)? _connect;
  final LiveAttestationFetcher _attestation;
  final Duration reauthInterval;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _sub;
  Timer? _reauthTimer;
  final _frames = StreamController<LiveServerFrame>.broadcast();
  final _pcm = StreamController<List<int>>.broadcast();

  Stream<LiveServerFrame> get frames => _frames.stream;
  Stream<List<int>> get pcm => _pcm.stream;
  bool get isConnected => _channel != null;

  String get _backendUrl => _ref.read(backendUrlProvider);

  Uri get _uri {
    final base = _backendUrl.replaceFirst(RegExp(r'^http'), 'ws');
    return Uri.parse('$base/api/v3/voice/live');
  }

  /// Opens the socket and sends the `auth` frame as its first message.
  ///
  /// The server allows 5 s from accept to a valid `auth` frame (`_AUTH_TIMEOUT_S`). A cold
  /// token refresh plus attestation (`/auth/nonce` and Play Integrity / App Attest) can take
  /// longer than that, so both are fetched *before* connecting (INT-014); once the socket is
  /// open the auth frame goes out with nothing left to wait for.
  ///
  /// Subscribe to [frames] before calling this: a server that refuses the socket at once
  /// (4503 feature off, 4429 session held) can close it before this returns, and a broadcast
  /// stream drops what nobody is listening to (INT-015).
  Future<void> connectAndAuth() async {
    await close();
    final tokens = _ref.read(tokenManagerProvider);
    final token = await tokens.getValidAccessToken();
    if (token == null || token.isEmpty) {
      throw StateError('no access token for live voice');
    }
    final attestation = await _attestation(_backendUrl);

    final channel = (_connect ?? WebSocketChannel.connect)(_uri);
    _channel = channel;
    _sub = channel.stream.listen(
      _onMessage,
      onError: (Object e, StackTrace st) {
        debugPrint('[LiveVoiceSocket] stream error: $e');
        if (!_frames.isClosed) {
          _frames.add(LiveErrorFrame(error: 'socket', message: e.toString()));
        }
      },
      // Fires only when the *server* (or the network) ends the socket: [close] cancels this
      // subscription before closing the sink. It used to only print, so the screen stayed on
      // "listening" with the mic streaming into a dead socket.
      onDone: () {
        final code = channel.closeCode;
        debugPrint('[LiveVoiceSocket] closed by server ($code)');
        _reauthTimer?.cancel();
        if (identical(_channel, channel)) _channel = null;
        if (!_frames.isClosed) _frames.add(errorForCloseCode(code));
      },
    );

    send(LiveAuthFrame(token: token, attestation: attestation));
    _reauthTimer?.cancel();
    _reauthTimer = Timer.periodic(reauthInterval, (_) => unawaited(reauth()));
  }

  Future<void> reauth() async {
    final channel = _channel;
    if (channel == null) return;
    final tokens = _ref.read(tokenManagerProvider);
    final token = await tokens.getAccessTokenValidFor(reauthMinValidity);
    if (token == null || token.isEmpty) return;
    final attestation = await _attestation(_backendUrl);
    // The socket may have been closed or replaced while the token was renewed; a reauth
    // landing on a new socket before its auth frame would be a protocol error there.
    if (!identical(_channel, channel)) return;
    send(LiveReauthFrame(token: token, attestation: attestation));
  }

  void start({
    required String sessionId,
    String language = 'en',
    String? voice,
  }) {
    send(LiveStartFrame(sessionId: sessionId, language: language, voice: voice));
  }

  void send(LiveClientFrame frame) {
    _channel?.sink.add(frame.encode());
  }

  void sendPcm(List<int> bytes) {
    _channel?.sink.add(bytes);
  }

  void bargeIn() => send(const LiveBargeInFrame());
  void commit() => send(const LiveCommitFrame());
  void confirm(List<String> approvalIds) => send(LiveConfirmFrame(approvalIds: approvalIds));
  void reject() => send(const LiveRejectFrame());
  void stop() => send(const LiveStopFrame());

  void _onMessage(dynamic data) {
    if (data is List<int>) {
      _pcm.add(data);
      return;
    }
    if (data is String) {
      try {
        final decoded = jsonDecode(data);
        final frame = LiveServerFrame.tryParse(decoded);
        if (frame != null) _frames.add(frame);
      } catch (e) {
        debugPrint('[LiveVoiceSocket] bad json: $e');
      }
    }
  }

  Future<void> close() async {
    _reauthTimer?.cancel();
    _reauthTimer = null;
    await _sub?.cancel();
    _sub = null;
    try {
      await _channel?.sink.close();
    } catch (_) {}
    _channel = null;
  }

  Future<void> dispose() async {
    await close();
    await _frames.close();
    await _pcm.close();
  }
}

/// Builds the socket a live-voice session uses; overridden in tests with a fake channel.
typedef LiveVoiceSocketFactory = LiveVoiceSocket Function(Ref ref);

final liveVoiceSocketFactoryProvider = Provider<LiveVoiceSocketFactory>(
  (ref) => (r) => LiveVoiceSocket(r),
);

final liveVoiceSocketProvider = Provider<LiveVoiceSocket>((ref) {
  final socket = LiveVoiceSocket(ref);
  ref.onDispose(socket.dispose);
  return socket;
});
