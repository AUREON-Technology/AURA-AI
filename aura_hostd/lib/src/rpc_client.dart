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
  });

  final String wsUrl;
  final String authToken;
  final TaskHandler taskHandler;
  final Logger logger;
  final Duration minBackoff;
  final Duration maxBackoff;

  WebSocket? _socket;
  bool _stopRequested = false;

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
  }

  Future<void> _connectAndServe() async {
    logger('connecting to $wsUrl');
    final socket = await WebSocket.connect(wsUrl);
    _socket = socket;
    socket.add(jsonEncode({'type': 'auth', 'token': authToken}));

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
      case 'reopen_task':
        return taskHandler.reopenTask(params);
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
