import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../../../auth/token_manager.dart';
import '../../../config/config.dart';
import '../../../security/device_validation_service.dart';
import 'live_voice_protocol.dart';

/// WebSocket client for `/api/v3/voice/live`.
///
/// Auth mirrors [VoiceClient]: bearer token + attestation inside the `auth` /
/// `reauth` frames (WS clients cannot always set HTTP Authorization).
class LiveVoiceSocket {
  LiveVoiceSocket(this._ref, {WebSocketChannel Function(Uri uri)? connect});

  final Ref _ref;
  final WebSocketChannel Function(Uri uri)? _connect;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _sub;
  final _frames = StreamController<LiveServerFrame>.broadcast();
  final _pcm = StreamController<List<int>>.broadcast();

  Stream<LiveServerFrame> get frames => _frames.stream;
  Stream<List<int>> get pcm => _pcm.stream;
  bool get isConnected => _channel != null;

  Uri get _uri {
    final base = AppConfig.backendUrl.replaceFirst(RegExp(r'^http'), 'ws');
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
      onDone: () {
        debugPrint('[LiveVoiceSocket] closed');
      },
    );

    final tokens = _ref.read(tokenManagerProvider);
    final token = await tokens.getValidAccessToken();
    if (token == null || token.isEmpty) {
      throw StateError('no access token for live voice');
    }
    final attestation = await DeviceValidationService.getAttestationHeaders();
    send(LiveAuthFrame(token: token, attestation: attestation));
  }

  Future<void> reauth() async {
    final tokens = _ref.read(tokenManagerProvider);
    final token = await tokens.getValidAccessToken();
    if (token == null || token.isEmpty) return;
    final attestation = await DeviceValidationService.getAttestationHeaders();
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
  void confirm(List<String> tools) => send(LiveConfirmFrame(tools: tools));
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
