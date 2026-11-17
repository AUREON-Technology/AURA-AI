/// Real WebSocket client for `aura_hostd`'s `/debug` route (D7 Part A.3) -- mirrors
/// `hostd_terminal_client_io.dart`'s exact shape (discover port+token from `control.json` fresh
/// on every connect), since spawning a debuggable process and streaming bytes is the same
/// problem the terminal client already solved. This class carries raw DAP bytes only -- it does
/// not parse the Debug Adapter Protocol itself; see `desktop/debug/dap_client.dart` for that.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:aura_client/l10n/l10n.dart';
import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'hostd_control_client_io.dart' show discoverHostdControlEndpoint;
import 'hostd_error_codes.dart';

class HostdDebugUnavailable implements Exception {
  HostdDebugUnavailable(this.message);
  final String message;
  @override
  String toString() => message;
}

class HostdDebugClient {
  HostdDebugClient({WebSocketChannel Function(Uri uri, {Map<String, dynamic>? headers})? connect})
      : _connect = connect;

  final WebSocketChannel Function(Uri uri, {Map<String, dynamic>? headers})? _connect;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _sub;
  final _dapBytes = StreamController<Uint8List>.broadcast();
  final _processExitCode = StreamController<int>.broadcast();
  final _errors = StreamController<String>.broadcast();
  String? _sessionId;
  int? _pid;

  /// Raw DAP bytes relayed from the daemon's debugpy tunnel -- framing/parsing is
  /// `DapClient`'s job (`desktop/debug/dap_client.dart`), not this transport's.
  Stream<Uint8List> get dapBytes => _dapBytes.stream;
  Stream<int> get processExitCode => _processExitCode.stream;
  Stream<String> get errors => _errors.stream;
  String? get sessionId => _sessionId;
  int? get pid => _pid;
  bool get isConnected => _channel != null;

  /// Opens the WS connection and sends the `start` frame naming the script to debug. Throws
  /// [HostdDebugUnavailable] if the daemon isn't reachable at all -- distinct from a mid-session
  /// [errors] event (untrusted workspace, containment violation, debugpy failing to start), which
  /// arrives as a frame over an otherwise-successful connection.
  Future<void> connect({
    required String rootPath,
    required String scriptPath,
    List<String> args = const [],
  }) async {
    final endpoint = await discoverHostdControlEndpoint();
    if (endpoint == null) {
      throw HostdDebugUnavailable(currentL10n.coreHostdNotRunning);
    }
    final uri = Uri.parse(
      'ws://127.0.0.1:${endpoint.port}/debug?root=${Uri.encodeQueryComponent(rootPath)}',
    );
    final headers = {'Authorization': 'Bearer ${endpoint.token}'};
    final channel = _connect != null
        ? _connect(uri, headers: headers)
        : IOWebSocketChannel.connect(uri, headers: headers);
    _channel = channel;
    _sub = channel.stream.listen(_onMessage, onDone: () {
      if (!_processExitCode.isClosed) _processExitCode.add(-1);
    });
    channel.sink.add(jsonEncode({'type': 'start', 'script_path': scriptPath, 'args': args}));
  }

  /// Sends raw DAP-framed bytes (already `Content-Length: N\r\n\r\n<json>` framed by
  /// [DapClient]) into the tunnel.
  void sendDap(Uint8List bytes) {
    _channel?.sink.add(jsonEncode({'type': 'dap', 'data': base64Encode(bytes)}));
  }

  Future<void> close() async {
    try {
      _channel?.sink.add(jsonEncode({'type': 'close'}));
    } catch (_) {
      // Already gone -- nothing to notify.
    }
    await _sub?.cancel();
    _sub = null;
    try {
      await _channel?.sink.close();
    } catch (_) {}
    _channel = null;
  }

  Future<void> dispose() async {
    await close();
    await _dapBytes.close();
    await _processExitCode.close();
    await _errors.close();
  }

  void _onMessage(dynamic data) {
    if (data is! String) return;
    Map<String, dynamic> msg;
    try {
      msg = jsonDecode(data) as Map<String, dynamic>;
    } on FormatException {
      return;
    }
    switch (msg['type']) {
      case 'started':
        _sessionId = msg['session_id'] as String?;
        _pid = (msg['pid'] as num?)?.toInt();
      case 'dap':
        final b64 = msg['data'] as String?;
        if (b64 != null && !_dapBytes.isClosed) _dapBytes.add(base64Decode(b64));
      case 'process_exited':
        final code = (msg['exit_code'] as num?)?.toInt() ?? -1;
        if (!_processExitCode.isClosed) _processExitCode.add(code);
      case 'debugpy_disconnected':
        if (!_processExitCode.isClosed) _processExitCode.add(-1);
      case 'error':
        final message = hostdCodeMessage(
          msg['code'] as String?,
          msg['message'] as String? ?? 'unknown debug error',
          detail: msg['detail'] as String?,
        );
        if (!_errors.isClosed) _errors.add(message);
    }
  }
}
