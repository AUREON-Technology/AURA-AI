import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../../../auth/token_manager.dart';
import '../../../config/backend_url_provider.dart';
import '../../../security/device_validation_service.dart';
import 'live_voice_protocol.dart';

/// WebSocket client for `/api/v3/voice/live`.
///
/// Auth mirrors [VoiceClient]: bearer token + attestation inside the `auth` /
/// `reauth` frames (WS clients cannot always set HTTP Authorization).
class LiveVoiceSocket {
  LiveVoiceSocket(
    this._ref, {
    WebSocketChannel Function(Uri uri)? connect,
    this.reauthInterval = defaultReauthInterval,
  }) : _connect = connect;

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

  final Ref _ref;
  final WebSocketChannel Function(Uri uri)? _connect;
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

  Future<void> connectAndAuth() async {
    await close();
    final channel = (_connect ?? WebSocketChannel.connect)(_uri);
    _channel = channel;
    _sub = channel.stream.listen(
      _onMessage,
      onError: (Object e, StackTrace st) {
        debugPrint('[LiveVoiceSocket] stream error: $e');
        _frames.add(LiveErrorFrame(error: 'socket', message: e.toString()));
      },
      // Fires only when the *server* (or the network) ends the socket: [close] cancels this
      // subscription before closing the sink. It used to only print, so the screen stayed on
      // "listening" with the mic streaming into a dead socket.
      onDone: () {
        final code = channel.closeCode;
        debugPrint('[LiveVoiceSocket] closed by server ($code)');
        _reauthTimer?.cancel();
        if (identical(_channel, channel)) _channel = null;
        if (!_frames.isClosed) {
          _frames.add(LiveErrorFrame(
            error: code == closeUnauthorized ? 'session_expired' : 'socket_closed',
            message: code == closeUnauthorized
                ? 'Live voice ended because your sign-in needs renewing. Start it again.'
                : 'Live voice was disconnected. Start it again.',
          ));
        }
      },
    );

    final tokens = _ref.read(tokenManagerProvider);
    final token = await tokens.getValidAccessToken();
    if (token == null || token.isEmpty) {
      throw StateError('no access token for live voice');
    }
    final attestation = await DeviceValidationService.getAttestationHeaders(
      backendUrl: _backendUrl,
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
    final attestation = await DeviceValidationService.getAttestationHeaders(
      backendUrl: _backendUrl,
    );
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

final liveVoiceSocketProvider = Provider<LiveVoiceSocket>((ref) {
  final socket = LiveVoiceSocket(ref);
  ref.onDispose(socket.dispose);
  return socket;
});
