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
import 'dart:math';

import 'config.dart' show InvalidBackendUrlError, requireSecureBackendUrl;
import 'containment.dart';
import 'isolated_task_registry.dart';
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
    Random? random,
  }) : _random = random ?? Random();

  final String wsUrl;
  final String authToken;
  final TaskHandler taskHandler;
  final Logger logger;
  final Duration minBackoff;
  final Duration maxBackoff;
  final Random _random;
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
  bool _credentialsRejected = false;
  int? _lastCloseCode;
  bool _finished = false;

  /// True once the server closed with [closeUnauthorized] and [run] gave up (audit INT-003).
  /// Reported in the control server's `/status` so the desktop UI can offer a re-pair instead of
  /// showing "reconnecting..." for a daemon that will never reconnect with this token.
  bool get credentialsRejected => _credentialsRejected;

  /// The close code of the most recent connection, or `null` before the first one closed.
  int? get lastCloseCode => _lastCloseCode;

  /// True once [run] has returned -- after [stop], or after a [closeUnauthorized] rejection. A
  /// finished client never connects again; the daemon must build a new one (with a new token).
  bool get isFinished => _finished;

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

  /// The wait before the next attempt: [backoff] scaled to between half and all of itself, so
  /// daemons that lost the same connection do not all return in the same instant. There is a herd
  /// to spread: the gateway redeploys on every push to `main`, and every paired computer
  /// reconnects at that moment (audit AUD-16). Never longer than [backoff], so [maxBackoff] still
  /// bounds it.
  static Duration jittered(Duration backoff, Random random) =>
      Duration(milliseconds: (backoff.inMilliseconds * (0.5 + random.nextDouble() * 0.5)).round());

  /// Runs the connect/serve/reconnect loop until [stop] is called. Each connection attempt that
  /// fails or drops backs off exponentially, capped at [maxBackoff], with each wait [jittered].
  ///
  /// A close with [closeUnauthorized] ends the loop instead: the server is saying this daemon's
  /// token is expired, revoked or no longer permitted, and reconnecting with the same token can
  /// only be refused again. Retrying used to hammer the server once a second forever, because a
  /// server-initiated close counted as a "clean" connection and reset the backoff.
  Future<void> run() async {
    try {
      await _runLoop();
    } finally {
      _finished = true;
    }
  }

  Future<void> _runLoop() async {
    // F53: the first frame is the device token. A plain ws:// URL to another host would send it
    // in clear on every reconnect, so such a client never connects at all.
    try {
      requireSecureBackendUrl(wsUrl);
    } on InvalidBackendUrlError catch (error) {
      logger('not connecting: ${error.message}');
      return;
    }
    var backoff = minBackoff;
    while (!_stopRequested) {
      try {
        final connectedAt = DateTime.now();
        final closeCode = await _connectAndServe();
        _lastCloseCode = closeCode;
        if (closeCode == closeUnauthorized) {
          _credentialsRejected = true;
          logger('the server refused this daemon\'s credentials (expired, revoked, or not '
              'permitted) -- pair it again from the AURA app or with `aura_hostd auth pair`');
          break;
        }
        // Only a connection that actually stayed up resets the backoff; one the server drops
        // straight away is a failure to back off from, not a success.
        if (DateTime.now().difference(connectedAt) >= healthyConnection) backoff = minBackoff;
      } catch (error) {
        logger('connection error: $error');
      }
      if (_stopRequested) break;
      final wait = jittered(backoff, _random);
      logger('reconnecting in ${(wait.inMilliseconds / 1000).toStringAsFixed(1)}s');
      await Future.delayed(wait);
      backoff = Duration(milliseconds: (backoff.inMilliseconds * 2).clamp(0, maxBackoff.inMilliseconds));
    }
  }

  /// Kills every command this client's handler still has running (the daemon is going away).
  Future<void> stopRunningCommands() => taskHandler.stopRunningCommands();

  void stop() {
    _stopRequested = true;
    _socket?.close();
    _isConnected = false;
  }

  /// The server's close code for a refused or no-longer-valid credential (`_CLOSE_UNAUTHORIZED`
  /// in aura_backend/aura_core/api/host_agent.py).
  static const closeUnauthorized = 4401;

  /// How long a connection must last before its end counts as a clean disconnect.
  static const healthyConnection = Duration(seconds: 30);

  /// Connects and serves until the socket closes; returns the socket's close code.
  Future<int?> _connectAndServe() async {
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
        } else if (frame['type'] == 'cancel') {
          // The server gave up on a call (its tool timed out, or the turn ended). If that call
          // is waiting for the human's approval, stop waiting and never run it (audit FSA-041).
          final id = frame['id'];
          if (id != null) taskHandler.cancel(id.toString());
        }
        // Any other frame type is ignored -- forward compatible with a newer server sending a
        // frame this daemon version does not understand.
      }
    } finally {
      _isConnected = false;
      // INT-007: the backend has already failed every call on this connection; nothing still
      // waiting for a human's approval may run after the drop.
      taskHandler.cancelAll();
    }
    logger('disconnected');
    return socket.closeCode;
  }

  Future<void> _handleCall(WebSocket socket, Map<String, dynamic> frame) async {
    final id = frame['id'];
    final method = frame['method'] as String?;
    final params = (frame['params'] as Map<String, dynamic>?) ?? const {};
    Map<String, Object?> reply;
    try {
      final result = await _dispatch(socket, id, method, params);
      reply = {'type': 'result', 'id': id, 'result': result};
    } catch (error) {
      // `code` is additive (`error_codes.dart`): the backend reads only `message`.
      final code = switch (error) {
        RpcError(code: final String code) => code,
        ContainmentViolation() => error.code,
        // WP4: an isolated task created without a cwd meets this on its first call.
        IsolationNeedsGitError() => error.code,
        _ => null,
      };
      final detail = error is RpcError ? error.detail : null;
      reply = {
        'type': 'error',
        'id': id,
        'message': error.toString(),
        if (code != null) 'code': code,
        if (detail != null) 'detail': detail,
      };
    }
    _send(socket, reply);
  }

  /// Sends [frame] on [socket] only while it is still open, and never lets a send failure escape
  /// (audit INT-019). A call that finishes after its connection dropped -- a long `run`, or an
  /// approval cancelled by the drop -- has nobody left to answer: the backend failed it when the
  /// socket closed. Writing to the closed socket raised an error nothing awaited, which could
  /// take the whole daemon down.
  void _send(WebSocket socket, Map<String, Object?> frame) {
    if (socket.readyState != WebSocket.open) {
      logger('dropping a "${frame['type']}" frame for call ${frame['id']}: its connection has '
          'closed');
      return;
    }
    try {
      socket.add(jsonEncode(frame));
    } catch (error) {
      logger('could not send a "${frame['type']}" frame for call ${frame['id']}: $error');
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
            _send(socket, {'type': 'stream', 'id': id, 'stream': streamName, 'data': data});
          },
          callId: id?.toString(),
        );
      case 'write_file':
        return taskHandler.writeFile(params, callId: id?.toString());
      case 'write_files':
        return taskHandler.writeFiles(params, callId: id?.toString());
      case 'read_file':
        return taskHandler.readFile(params);
      case 'list_roots':
        return taskHandler.listRoots(params);
      case 'init_git':
        return taskHandler.initGit(params);
      default:
        throw RpcError('unknown method "$method"');
    }
  }
}
