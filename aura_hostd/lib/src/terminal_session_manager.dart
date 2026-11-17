/// Owns every live [PtySessionInterface] session (Windows [PtySession] or
/// POSIX `PosixPtySession`, see `pty_session_interface.dart`) created through
/// the `/terminal` WebSocket route on [ControlServer]'s loopback HTTP server
/// (see control_server.dart).
///
/// One connection = one session, created only once the client sends a
/// `start` frame carrying the initial size -- the WebSocket itself is
/// upgraded first so trust-gate failures can be reported as a clean `error`
/// frame rather than an HTTP rejection the client has to interpret.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'error_codes.dart';
import 'pty_session.dart';
import 'pty_session_interface.dart';
import 'posix_pty_session.dart';
import 'workspace_store.dart';
import 'containment.dart' show WorkspaceRoot;

/// Logs one line of terminal-session lifecycle (start/exit/error) so a
/// stuck or misbehaving session is visible in the daemon's own output
/// instead of only surfacing as a client-side symptom.
typedef TerminalSessionLogger = void Function(String message);

void _defaultLogger(String message) => stdout.writeln('[aura_hostd] $message');

class TerminalSessionManager {
  TerminalSessionManager(this._workspaceStore, {TerminalSessionLogger? logger})
      : _logger = logger ?? _defaultLogger;

  final WorkspaceStore _workspaceStore;
  final TerminalSessionLogger _logger;
  final Map<String, PtySessionInterface> _sessions = {};

  /// Handles one `/terminal` WebSocket connection end to end: trust-checks
  /// [rootPath] against granted roots (same bar `command_policy.dart` uses
  /// for `run_command` -- a full interactive shell is at least as powerful),
  /// then processes `start`/`input`/`resize`/`close` frames until the socket
  /// closes, always tearing down its [PtySession] on the way out.
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

    PtySessionInterface? session;
    StreamSubscription<Uint8List>? outputSub;

    Future<void> teardown() async {
      final s = session;
      if (s == null) return;
      _sessions.remove(s.sessionId);
      await outputSub?.cancel();
      await s.dispose();
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
            if (session != null) continue;
            final cols = (msg['cols'] as num?)?.toInt() ?? 80;
            final rows = (msg['rows'] as num?)?.toInt() ?? 24;
            final sessionId = 'pty_${DateTime.now().microsecondsSinceEpoch}';
            // Windows gets the proven Win32 ConPTY backend; Linux/macOS get the POSIX backend
            // (posix_pty_session.dart) -- UNTESTED as of 2026-09-25, see that file's own doc
            // comment. Both implement PtySessionInterface, so nothing below this branch cares
            // which one it got.
            final PtySessionInterface s = Platform.isWindows
                ? PtySession(
                    sessionId: sessionId,
                    workingDirectory: root.canonicalPath,
                    initialCols: cols,
                    initialRows: rows,
                  )
                : PosixPtySession(
                    sessionId: sessionId,
                    workingDirectory: root.canonicalPath,
                    initialCols: cols,
                    initialRows: rows,
                  );
            try {
              await s.start();
            } catch (error) {
              _logger('terminal $sessionId failed to start: $error');
              await _sendError(
                socket,
                HostdErrorCode.terminalStartFailed,
                'failed to start terminal: $error',
                detail: '$error',
              );
              continue;
            }
            _sessions[sessionId] = s;
            session = s;
            _logger('terminal $sessionId started (pid ${s.pid}, root $rootPath)');
            socket.add(jsonEncode({'type': 'started', 'session_id': sessionId, 'pid': s.pid}));
            outputSub = s.output.listen((chunk) {
              socket.add(jsonEncode({'type': 'output', 'data': base64Encode(chunk)}));
            });
            unawaited(s.exitCode.then((code) {
              _logger('terminal $sessionId exited (code $code)');
              try {
                socket.add(jsonEncode({'type': 'exited', 'exit_code': code}));
              } catch (_) {
                // Socket already closed -- nothing to notify.
              }
            }));
          case 'input':
            final b64 = msg['data'] as String?;
            if (session != null && b64 != null) session.write(base64Decode(b64));
          case 'resize':
            final cols = (msg['cols'] as num?)?.toInt();
            final rows = (msg['rows'] as num?)?.toInt();
            if (session != null && cols != null && rows != null) session.resize(cols, rows);
          case 'close':
            await teardown();
            await socket.close();
            return;
        }
      }
    } finally {
      // Reached whether the client sent "close", the socket just dropped, or
      // an error propagated out of the loop -- a session must never outlive
      // its connection.
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
