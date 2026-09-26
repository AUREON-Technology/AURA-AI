// Exercises HostdTerminalClient against a real local WebSocket server standing in for
// aura_hostd's `/terminal` route (terminal_session_manager.dart) -- same frame shapes that file
// sends. Imports hostd_terminal_client_io.dart directly for the same reason
// hostd_control_client_io_test.dart imports its VM-only counterpart directly.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:aura_client/core/hostd/hostd_control_client_io.dart' show HostdControlEndpoint;
import 'package:aura_client/core/hostd/hostd_terminal_client_io.dart';
import 'package:flutter_test/flutter_test.dart';

/// Stands in for aura_hostd's `/terminal` WebSocket route. `sendFrames` runs once a client
/// connects; the server closes the socket right after sending everything it returns, mirroring
/// `terminal_session_manager.dart` sending an `error` frame and immediately closing for an
/// untrusted/ungranted root.
class _FakeTerminalServer {
  _FakeTerminalServer._(this._httpServer);

  final HttpServer _httpServer;

  static Future<_FakeTerminalServer> start({
    required List<Map<String, dynamic>> Function() frames,
  }) async {
    final httpServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    unawaited(() async {
      await for (final request in httpServer) {
        final socket = await WebSocketTransformer.upgrade(request);
        for (final frame in frames()) {
          socket.add(jsonEncode(frame));
        }
        await socket.close();
      }
    }());
    return _FakeTerminalServer._(httpServer);
  }

  int get port => _httpServer.port;

  Future<void> close() => _httpServer.close(force: true);
}

void main() {
  test('an error frame followed by the server closing does not emit a synthetic exit code',
      () async {
    final server = await _FakeTerminalServer.start(
      frames: () => [
        {'type': 'error', 'message': 'workspace is not trusted -- run `aura_hostd trust` first'},
      ],
    );
    addTearDown(server.close);
    final client = HostdTerminalClient(
      discoverEndpoint: () async => HostdControlEndpoint(port: server.port, token: 't'),
    );

    final errors = <String>[];
    final exitCodes = <int>[];
    client.errors.listen(errors.add);
    client.exitCode.listen(exitCodes.add);

    await client.connect(rootPath: '/some/path', cols: 80, rows: 24);
    // Give the fake server's frames + the socket close time to arrive and be processed.
    await Future<void>.delayed(const Duration(milliseconds: 200));

    expect(errors, ['workspace is not trusted -- run `aura_hostd trust` first']);
    expect(exitCodes, isEmpty);

    await client.dispose();
  });

  test('a real exited frame is not followed by a synthetic exit code', () async {
    final server = await _FakeTerminalServer.start(
      frames: () => [
        {'type': 'exited', 'exit_code': 0},
      ],
    );
    addTearDown(server.close);
    final client = HostdTerminalClient(
      discoverEndpoint: () async => HostdControlEndpoint(port: server.port, token: 't'),
    );

    final exitCodes = <int>[];
    client.exitCode.listen(exitCodes.add);

    await client.connect(rootPath: '/some/path', cols: 80, rows: 24);
    await Future<void>.delayed(const Duration(milliseconds: 200));

    expect(exitCodes, [0]);

    await client.dispose();
  });

  test('the socket closing with no prior outcome frame still emits a synthetic exit code -1',
      () async {
    final server = await _FakeTerminalServer.start(frames: () => const []);
    addTearDown(server.close);
    final client = HostdTerminalClient(
      discoverEndpoint: () async => HostdControlEndpoint(port: server.port, token: 't'),
    );

    final exitCodes = <int>[];
    client.exitCode.listen(exitCodes.add);

    await client.connect(rootPath: '/some/path', cols: 80, rows: 24);
    await Future<void>.delayed(const Duration(milliseconds: 200));

    expect(exitCodes, [-1]);

    await client.dispose();
  });
}
