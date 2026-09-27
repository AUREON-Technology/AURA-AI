/// Outbound WebSocket client to the backend's `/api/v3/host/agent`
/// (`aura_backend/aura_core/api/host_agent.py`).
///
/// Connects outbound deliberately -- ADR-011 explains why: a machine behind NAT with no forwarded
/// port still works, the same reasoning `voice/live` uses for its server-push direction. The
/// server then calls *into* this connection; this client's job is to answer `call` frames with
/// `result`/`error` frames, using exactly the frame shapes `HostAgentSession`
/// (`aura_backend/aura_core/adapters/host_agent.py`) expects:
///
/// - outbound (this daemon -> server): `{"type": "auth", "token": ..., "attestation": {...}}`,
///   then `{"type": "result", "id": ..., "result": {...}}` or
///   `{"type": "error", "id": ..., "message": ...}` for each `call` received.
/// - inbound (server -> this daemon): `{"type": "call", "id": ..., "method": ..., "params": {...}}`.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'task_handler.dart';

typedef Logger = void Function(String message);

class HostAgentClient {
  HostAgentClient({
    required this.wsUrl,
    required this.authToken,
    required this.taskHandler,
    this.logger = _defaultLogger,
    this.minBackoff = const Duration(seconds: 1),
    this.maxBackoff = const Duration(seconds: 30),
    this.pingInterval = const Duration(seconds: 20),
  });

  final String wsUrl;
  final String authToken;
  final TaskHandler taskHandler;
  final Logger logger;
  final Duration minBackoff;
  final Duration maxBackoff;
  /// How often to send a protocol-level WebSocket ping while otherwise idle (`run_command` etc.
  /// only flow when the model calls a tool -- a session sitting between turns sends nothing for
  /// however long that takes). Set on `dart:io`'s `WebSocket` itself (`_socket.pingInterval`
  /// below), so this rides the raw ping/pong control frames every WS implementation already
  /// handles transparently -- it never touches the `{"type": ...}` JSON message frames `_onMessage`
  /// parses, and needs no change on the backend (any conformant WS server, uvicorn included,
  /// answers a ping with a pong automatically). Confirmed live: a paired session went
  /// connected -> disconnected -> reconnected with no application activity in between, exactly
  /// the symptom an idle connection with nothing keeping it alive produces.
  final Duration pingInterval;

  WebSocket? _socket;
  bool _stopRequested = false;
  bool _isConnected = false;

  /// True while the outbound socket to the backend is open. This is the daemon's only signal
  /// that `HostAgentRegistry` (backend) currently has a live session for it -- the wire protocol
  /// has no explicit "auth accepted" ack frame, but the backend closes the socket immediately on
  /// auth failure (`api/host_agent.py`), so an open socket reliably means registered, modulo the
  /// brief window between `WebSocket.connect` returning and the server processing the auth frame.
  /// Exposed so `control_server.dart`'s `/status` can report it distinctly from "have a stored
  /// token" -- those disagree for as long as a reconnect is in backoff after a dropped
  /// connection (e.g. a backend redeploy), during which every tool call still fails with "no
  /// desktop app is connected" even though the daemon believes itself paired.
  bool get isConnected => _isConnected;

  /// The live socket's own ping interval, once connected -- exposed only so a test can confirm
  /// [pingInterval] actually reached the socket, not merely that this class stored the
  /// constructor argument. `null` before the first connection.
  Duration? get socketPingInterval => _socket?.pingInterval;

  static void _defaultLogger(String message) => stdout.writeln('[aura_hostd] $message');

  /// Runs the connect/serve/reconnect loop until [stop] is called. Each connection attempt that
  /// fails or drops backs off with jitter-free exponential backoff, capped at [maxBackoff] --
  /// simple and predictable rather than clever, since this only ever has one peer (the backend)
  /// and no thundering-herd concern to jitter against.
  Future<void> run() async {
    var backoff = minBackoff;
    while (!_stopRequested) {
      try {
        await _connectAndServe();
        backoff = minBackoff; // a clean connection resets backoff
      } catch (error) {
        logger('connection error: $error');
      }
      if (_stopRequested) break;
      logger('reconnecting in ${backoff.inSeconds}s');
      await Future.delayed(backoff);
      backoff = Duration(milliseconds: (backoff.inMilliseconds * 2).clamp(0, maxBackoff.inMilliseconds));
    }
  }

  void stop() {
    _stopRequested = true;
    _socket?.close();
    _isConnected = false;
  }

  Future<void> _connectAndServe() async {
    logger('connecting to $wsUrl');
    final socket = await WebSocket.connect(wsUrl);
    socket.pingInterval = pingInterval;
    _socket = socket;
    _isConnected = true;
    socket.add(jsonEncode({'type': 'auth', 'token': authToken}));

    try {
      await for (final message in socket) {
        if (message is! String) continue;
        Map<String, dynamic> frame;
        try {
          final decoded = jsonDecode(message);
          if (decoded is! Map<String, dynamic>) continue;
          frame = decoded;
        } on FormatException {
          continue;
        }
        if (frame['type'] == 'call') {
          // Deliberately not awaited: a slow `run_command` must not block the auth-frame /
          // heartbeat plumbing for other in-flight calls, and each call's own result carries its
          // own correlation id so out-of-order completion is fine on the wire.
          unawaited(_handleCall(socket, frame));
        }
        // Any other frame type (there are none yet besides `call`) is ignored -- forward
        // compatible with a newer server sending a frame this daemon version does not understand.
      }
    } finally {
      _isConnected = false;
    }
    logger('disconnected');
  }

  Future<void> _handleCall(WebSocket socket, Map<String, dynamic> frame) async {
    final id = frame['id'];
    final method = frame['method'] as String?;
    final params = (frame['params'] as Map<String, dynamic>?) ?? const {};
    try {
      final result = await _dispatch(socket, id, method, params);
      socket.add(jsonEncode({'type': 'result', 'id': id, 'result': result}));
    } catch (error) {
      socket.add(jsonEncode({'type': 'error', 'id': id, 'message': error.toString()}));
    }
  }

  Future<Map<String, Object?>> _dispatch(
    WebSocket socket, dynamic id, String? method, Map<String, dynamic> params,
  ) {
    switch (method) {
      case 'create_task':
        return taskHandler.createTask(params);
      case 'run':
        // A `stream` frame per output chunk, sent as it arrives -- the caller
        // (HostAgentSandboxRunner.run_streaming, aura_backend) reads these as they're delivered,
        // not only once this call's own `result` frame (sent by `_handleCall` above, after this
        // returns) arrives. Every other method has exactly one outcome and needs no interim
        // frames, so this is `run`-specific rather than a general dispatch feature.
        return taskHandler.run(
          params,
          onChunk: (streamName, data) {
            socket.add(jsonEncode({'type': 'stream', 'id': id, 'stream': streamName, 'data': data}));
          },
        );
      case 'write_file':
        return taskHandler.writeFile(params);
      case 'write_files':
        return taskHandler.writeFiles(params);
      case 'read_file':
        return taskHandler.readFile(params);
      default:
        throw RpcError('unknown method "$method"');
    }
  }
}
