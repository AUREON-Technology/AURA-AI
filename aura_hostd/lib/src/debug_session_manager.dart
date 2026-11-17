/// Owns every live debug session created through the `/debug` WebSocket route on [ControlServer]
/// (D7 Part A.3) -- mirrors `terminal_session_manager.dart`'s shape exactly (one WS connection,
/// a `start` frame carrying what's needed, trust-gate failures reported as a clean `error` frame
/// rather than an HTTP rejection), since spawning a debuggable process and streaming bytes
/// bidirectionally is the same problem the terminal already solved.
///
/// **This class never parses the Debug Adapter Protocol.** It spawns `debugpy` (Python's
/// standard DAP server, `python -m debugpy --listen ... --wait-for-client`) and, once it accepts
/// a TCP connection, proxies raw bytes both directions as base64 inside `{"type":"dap","data":
/// ...}` WebSocket frames -- transport only, exactly like the terminal proxies PTY bytes without
/// interpreting ANSI escapes. DAP framing/semantics are the client's job
/// (`hostd_debug_client_io.dart` in `aura_client`).
///
/// A debug session can execute arbitrary Python inside the target script -- at least as capable
/// as a shell -- so it requires `root.trusted` exactly like `/terminal` does, no separate policy
/// path.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'containment.dart' show ContainmentViolation, WorkspaceRoot;
import 'error_codes.dart';
import 'workspace_store.dart';

typedef DebugSessionLogger = void Function(String message);

void _defaultLogger(String message) => stdout.writeln('[aura_hostd] $message');

/// How long to wait for `debugpy` to bind and accept a connection before giving up -- generous
/// since a cold Python interpreter start (importing debugpy itself) can take a moment, but bounded
/// so a debugpy that will never come up doesn't hang the connection forever.
const _connectTimeout = Duration(seconds: 15);

class _DebugSession {
  _DebugSession({required this.process, required this.socket});
  final Process process;
  final Socket socket;
}

class DebugSessionManager {
  DebugSessionManager(this._workspaceStore, {DebugSessionLogger? logger}) : _logger = logger ?? _defaultLogger;

  final WorkspaceStore _workspaceStore;
  final DebugSessionLogger _logger;
  final Map<String, _DebugSession> _sessions = {};

  Future<void> handleConnection(WebSocket socket, {required String rootPath}) async {
    final roots = _workspaceStore.load();
    WorkspaceRoot? root;
    for (final r in roots.all) {
      if (r.canonicalPath == rootPath) {
        root = r;
        break;
      }
    }
    if (root == null) {
      await _sendError(socket, HostdErrorCode.rootNotGranted, '$rootPath is not a granted root');
      await socket.close();
      return;
    }
    if (!root.trusted) {
      await _sendError(
        socket,
        HostdErrorCode.workspaceNotTrusted,
        'workspace is not trusted -- run `aura_hostd trust "$rootPath"` first',
      );
      await socket.close();
      return;
    }

    Process? process;
    Socket? tcpSocket;
    StreamSubscription<Uint8List>? tcpSub;
    String? sessionId;

    Future<void> teardown() async {
      if (sessionId != null) _sessions.remove(sessionId);
      await tcpSub?.cancel();
      tcpSocket?.destroy();
      process?.kill();
    }

    try {
      await for (final data in socket) {
        if (data is! String) continue;
        Map<String, dynamic> msg;
        try {
          msg = jsonDecode(data) as Map<String, dynamic>;
        } on FormatException {
          continue;
        }
        switch (msg['type'] as String?) {
          case 'start':
            if (process != null) continue;
            final scriptPath = msg['script_path'] as String?;
            if (scriptPath == null) {
              await _sendError(socket, HostdErrorCode.missingParameter, 'script_path is required');
              continue;
            }
            String resolvedScript;
            try {
              resolvedScript = roots.requireContained(root, scriptPath);
            } on ContainmentViolation catch (error) {
              await _sendError(socket, error.code, error.message);
              continue;
            }
            final args = (msg['args'] as List?)?.cast<Object?>().map((e) => e.toString()).toList() ?? const [];

            final port = await _findFreePort();
            try {
              process = await Process.start(
                'python',
                ['-m', 'debugpy', '--listen', '127.0.0.1:$port', '--wait-for-client', resolvedScript, ...args],
                workingDirectory: root.canonicalPath,
              );
            } catch (error) {
              await _sendError(
                socket,
                HostdErrorCode.debugStartFailed,
                'failed to start debugpy: $error',
                detail: '$error',
              );
              process = null;
              continue;
            }
            final startedProcess = process;
            unawaited(startedProcess.exitCode.then((code) {
              _logger('debug session (pid ${startedProcess.pid}) process exited (code $code)');
              try {
                socket.add(jsonEncode({'type': 'process_exited', 'exit_code': code}));
              } catch (_) {
                // Socket already closed -- nothing to notify.
              }
            }));

            try {
              tcpSocket = await _connectWithRetry(port);
            } catch (error) {
              await _sendError(
                socket,
                HostdErrorCode.debugUnreachable,
                'debugpy did not become reachable on port $port: $error',
                detail: '$error',
              );
              startedProcess.kill();
              process = null;
              continue;
            }

            sessionId = 'dbg_${DateTime.now().microsecondsSinceEpoch}';
            _sessions[sessionId] = _DebugSession(process: startedProcess, socket: tcpSocket);
            _logger('debug $sessionId started (pid ${startedProcess.pid}, root $rootPath, script $resolvedScript)');
            socket.add(jsonEncode({'type': 'started', 'session_id': sessionId, 'pid': startedProcess.pid}));

            tcpSub = tcpSocket.listen(
              (bytes) {
                try {
                  socket.add(jsonEncode({'type': 'dap', 'data': base64Encode(bytes)}));
                } catch (_) {
                  // Socket already closed -- nothing to notify.
                }
              },
              onDone: () {
                try {
                  socket.add(jsonEncode({'type': 'debugpy_disconnected'}));
                } catch (_) {
                  // Socket already closed -- nothing to notify.
                }
              },
            );
          case 'dap':
            final b64 = msg['data'] as String?;
            if (tcpSocket != null && b64 != null) tcpSocket.add(base64Decode(b64));
          case 'close':
            await teardown();
            await socket.close();
            return;
        }
      }
    } finally {
      // Reached whether the client sent "close", the socket just dropped, or an error propagated
      // out of the loop -- a spawned debugpy process must never outlive its connection.
      await teardown();
    }
  }

  /// [message] is the English text apps predating codes show; [code] (`error_codes.dart`) is
  /// what a newer app translates.
  /// [detail] is the underlying error alone (no English prefix), for a message that wraps one.
  Future<void> _sendError(WebSocket socket, String code, String message, {String? detail}) async {
    try {
      socket.add(jsonEncode({
        'type': 'error',
        'message': message,
        'code': code,
        if (detail != null) 'detail': detail,
      }));
    } catch (_) {
      // Socket already closed -- nothing to notify.
    }
  }
}

/// Binds an ephemeral port and immediately releases it so `debugpy --listen` can bind the same
/// number -- a small, accepted TOCTOU window (same pattern Flutter's own tooling uses to pick a
/// free port for its VM service), fine here since nothing else on this loopback-only machine is
/// racing to grab it in that instant.
Future<int> _findFreePort() async {
  final probe = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
  final port = probe.port;
  await probe.close();
  return port;
}

Future<Socket> _connectWithRetry(int port) async {
  final deadline = DateTime.now().add(_connectTimeout);
  Object? lastError;
  while (DateTime.now().isBefore(deadline)) {
    try {
      return await Socket.connect(InternetAddress.loopbackIPv4, port, timeout: const Duration(seconds: 2));
    } catch (error) {
      lastError = error;
      await Future<void>.delayed(const Duration(milliseconds: 200));
    }
  }
  throw StateError('timed out waiting for debugpy to accept a connection: $lastError');
}
