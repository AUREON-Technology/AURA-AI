/// Real WebSocket client for `aura_hostd`'s `/terminal` route -- talks
/// directly to the loopback daemon (never the backend), mirroring how
/// `hostd_control_client_io.dart` reaches every other daemon endpoint:
/// discover port+token from `control.json` fresh on every connect, since the
/// daemon can restart independently of this app's own lifecycle.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:aura_client/l10n/l10n.dart';
import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'hostd_control_client_io.dart' show HostdControlEndpoint, discoverHostdControlEndpoint;

class HostdTerminalUnavailable implements Exception {
  HostdTerminalUnavailable(this.message);
  final String message;
  @override
  String toString() => message;
}

class HostdTerminalClient {
  HostdTerminalClient({
    WebSocketChannel Function(Uri uri, {Map<String, dynamic>? headers})? connect,
    Future<HostdControlEndpoint?> Function()? discoverEndpoint,
  })  : _connect = connect,
        _discoverEndpoint = discoverEndpoint ?? discoverHostdControlEndpoint;

  final WebSocketChannel Function(Uri uri, {Map<String, dynamic>? headers})? _connect;
  final Future<HostdControlEndpoint?> Function() _discoverEndpoint;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _sub;
  final _output = StreamController<Uint8List>.broadcast();
  final _exitCode = StreamController<int>.broadcast();
  final _errors = StreamController<String>.broadcast();
  String? _sessionId;
  // Set once a real 'exited' or 'error' frame has been processed, so the socket's `onDone`
  // below never overwrites a genuine outcome with a synthetic exit code -- see its own comment.
  bool _resolved = false;

  Stream<Uint8List> get output => _output.stream;
  Stream<int> get exitCode => _exitCode.stream;
  Stream<String> get errors => _errors.stream;
  String? get sessionId => _sessionId;
  bool get isConnected => _channel != null;

  /// Opens the WS connection and immediately sends the `start` frame. Throws
  /// [HostdTerminalUnavailable] if the daemon isn't reachable at all --
  /// distinct from a mid-session [errors] event (e.g. an untrusted
  /// workspace), which arrives as a frame over an otherwise-successful
  /// connection.
  Future<void> connect({required String rootPath, required int cols, required int rows}) async {
    final endpoint = await _discoverEndpoint();
    if (endpoint == null) {
      throw HostdTerminalUnavailable(currentL10n.coreHostdNotRunning);
    }
    final uri = Uri.parse(
      'ws://127.0.0.1:${endpoint.port}/terminal?root=${Uri.encodeQueryComponent(rootPath)}',
    );
    final headers = {'Authorization': 'Bearer ${endpoint.token}'};
    final channel = _connect != null
        ? _connect(uri, headers: headers)
        : IOWebSocketChannel.connect(uri, headers: headers);
    _channel = channel;
    _sub = channel.stream.listen(_onMessage, onDone: () {
      // The server sends an 'error' frame and closes the socket in the same breath for an
      // untrusted/ungranted root (terminal_session_manager.dart), so onDone fires right after
      // _onMessage already resolved the outcome -- only synthesize a fake exit code when the
      // close was genuinely unexplained (daemon crash, network drop), never overwrite a real one.
      if (!_resolved && !_exitCode.isClosed) {
        _exitCode.add(-1);
      }
    });
    channel.sink.add(jsonEncode({'type': 'start', 'cols': cols, 'rows': rows}));
  }

  void sendInput(Uint8List bytes) {
    _channel?.sink.add(jsonEncode({'type': 'input', 'data': base64Encode(bytes)}));
  }

  void resize(int cols, int rows) {
    _channel?.sink.add(jsonEncode({'type': 'resize', 'cols': cols, 'rows': rows}));
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
    await _output.close();
    await _exitCode.close();
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
      case 'output':
        final b64 = msg['data'] as String?;
        if (b64 != null && !_output.isClosed) _output.add(base64Decode(b64));
      case 'exited':
        final code = (msg['exit_code'] as num?)?.toInt() ?? -1;
        _resolved = true;
        if (!_exitCode.isClosed) _exitCode.add(code);
      case 'error':
        final message = msg['message'] as String? ?? 'unknown terminal error';
        _resolved = true;
        if (!_errors.isClosed) _errors.add(message);
    }
  }
}
