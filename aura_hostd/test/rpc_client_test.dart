// Integration test for HostAgentClient's wire protocol against a real local WebSocket server --
// no mocking of dart:io, so this exercises the exact frame shapes the backend
// (aura_backend/aura_core/api/host_agent.py, adapters/host_agent.py) sends and expects:
//
//   server -> daemon: {"type": "call", "id": ..., "method": ..., "params": {...}}
//   daemon -> server: {"type": "auth", "token": ...} first, then
//                      {"type": "result", "id": ..., "result": {...}} or
//                      {"type": "error", "id": ..., "message": ...}
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:aura_hostd/src/containment.dart';
import 'package:aura_hostd/src/rpc_client.dart';
import 'package:aura_hostd/src/task_handler.dart';
import 'package:test/test.dart';

class _FakeServer {
  _FakeServer(this._httpServer, this._socketCompleter);

  final HttpServer _httpServer;
  final Completer<WebSocket> _socketCompleter;

  static Future<_FakeServer> start() async {
    final httpServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final completer = Completer<WebSocket>();
    httpServer.listen((request) async {
      final socket = await WebSocketTransformer.upgrade(request);
      completer.complete(socket);
    });
    return _FakeServer(httpServer, completer);
  }

  int get port => _httpServer.port;
  String get wsUrl => 'ws://127.0.0.1:$port/api/v3/host/agent';

  Future<WebSocket> get clientSocket => _socketCompleter.future;

  Future<void> close() => _httpServer.close(force: true);
}

/// The server side's upgrade completes before the client's own `WebSocket.connect` future does:
/// the client still has to read the 101 response, which can take more than one event-loop turn
/// when the machine is busy (the whole suite runs files in parallel). Waits for [condition] for up
/// to 5s instead of a single zero-delay turn, which failed intermittently under load. The test's
/// assertion that follows is unchanged; this only stops it running before the client could know.
Future<void> _settle(bool Function() condition) async {
  final deadline = DateTime.now().add(const Duration(seconds: 5));
  while (!condition() && DateTime.now().isBefore(deadline)) {
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }
}

void main() {
  late _FakeServer server;

  setUp(() async {
    server = await _FakeServer.start();
  });

  tearDown(() async {
    await server.close();
  });

  test('sends an auth frame with the configured token immediately on connect', () async {
    final handler = TaskHandler(WorkspaceRoots(const []));
    final client = HostAgentClient(
      wsUrl: server.wsUrl,
      authToken: 'secret-token',
      taskHandler: handler,
      logger: (_) {},
    );
    unawaited(client.run());
    addTearDown(client.stop);

    final socket = await server.clientSocket;
    final firstFrame = jsonDecode(await socket.first as String) as Map<String, dynamic>;
    expect(firstFrame['type'], 'auth');
    expect(firstFrame['token'], 'secret-token');
  });

  test('answers a create_task call with a result frame carrying the same id', () async {
    final handler = TaskHandler(WorkspaceRoots(const []));
    final client = HostAgentClient(
      wsUrl: server.wsUrl,
      authToken: 't',
      taskHandler: handler,
      logger: (_) {},
    );
    unawaited(client.run());
    addTearDown(client.stop);

    final socket = await server.clientSocket;
    final frames = StreamQueue(socket.map((m) => jsonDecode(m as String) as Map<String, dynamic>));
    await frames.next; // auth frame

    socket.add(jsonEncode({'type': 'call', 'id': 'abc123', 'method': 'create_task', 'params': {}}));
    final response = await frames.next;

    expect(response['type'], 'result');
    expect(response['id'], 'abc123');
    expect((response['result'] as Map)['task_id'], isNotEmpty);
  });

  test('answers an unknown method with an error frame rather than dropping the connection',
      () async {
    final handler = TaskHandler(WorkspaceRoots(const []));
    final client = HostAgentClient(
      wsUrl: server.wsUrl,
      authToken: 't',
      taskHandler: handler,
      logger: (_) {},
    );
    unawaited(client.run());
    addTearDown(client.stop);

    final socket = await server.clientSocket;
    final frames = StreamQueue(socket.map((m) => jsonDecode(m as String) as Map<String, dynamic>));
    await frames.next; // auth frame

    socket.add(jsonEncode({'type': 'call', 'id': 'x1', 'method': 'delete_everything', 'params': {}}));
    final response = await frames.next;

    expect(response['type'], 'error');
    expect(response['id'], 'x1');
    expect(response['message'], contains('unknown method'));
  });

  test('a call for a path outside every granted root returns an error frame, not a crash',
      () async {
    final handler = TaskHandler(WorkspaceRoots(const []));
    final client = HostAgentClient(
      wsUrl: server.wsUrl,
      authToken: 't',
      taskHandler: handler,
      logger: (_) {},
    );
    unawaited(client.run());
    addTearDown(client.stop);

    final socket = await server.clientSocket;
    final frames = StreamQueue(socket.map((m) => jsonDecode(m as String) as Map<String, dynamic>));
    await frames.next; // auth frame

    socket.add(jsonEncode({
      'type': 'call',
      'id': 'w1',
      'method': 'write_file',
      'params': {'path': '/etc/whatever', 'content': 'x'},
    }));
    final response = await frames.next;

    expect(response['type'], 'error');
    expect(response['id'], 'w1');
  });

  test('a run call sends stream frames before the terminal result frame', () async {
    final tempDir = Directory.systemTemp.createTempSync('aura_hostd_rpc_stream_test_');
    addTearDown(() => tempDir.deleteSync(recursive: true));
    final roots = WorkspaceRoots(const []);
    roots.addRoot(tempDir.path, trusted: true);
    final handler = TaskHandler(roots);
    final client = HostAgentClient(
      wsUrl: server.wsUrl,
      authToken: 't',
      taskHandler: handler,
      logger: (_) {},
    );
    unawaited(client.run());
    addTearDown(client.stop);

    final socket = await server.clientSocket;
    final frames = StreamQueue(socket.map((m) => jsonDecode(m as String) as Map<String, dynamic>));
    await frames.next; // auth frame

    socket.add(jsonEncode({
      'type': 'call',
      'id': 'r1',
      'method': 'run',
      'params': {
        'command': ['sh', '-c', 'echo one; echo two'],
        'timeout_s': 5.0,
      },
    }));

    final received = <Map<String, dynamic>>[];
    Map<String, dynamic> frame;
    do {
      frame = await frames.next;
      expect(frame['id'], 'r1');
      received.add(frame);
    } while (frame['type'] == 'stream');

    expect(received.last['type'], 'result');
    // At least one interim `stream` frame arrived before the terminal `result` -- the whole
    // point of this call shape over the plain single-shot one.
    expect(received.where((f) => f['type'] == 'stream'), isNotEmpty);
    final streamedStdout = received
        .where((f) => f['type'] == 'stream' && f['stream'] == 'stdout')
        .map((f) => f['data'] as String)
        .join();
    expect(streamedStdout, contains('one'));
    expect(streamedStdout, contains('two'));
    expect((received.last['result'] as Map)['exit_code'], 0);
  }, testOn: '!windows');

  group('pingInterval', () {
    // A live session that goes connect -> disconnect -> reconnect with no application traffic in
    // between (confirmed against a real deployment) is exactly the symptom of an idle WebSocket
    // getting silently dropped -- the JSON message protocol only flows when a tool call is
    // active, which can be minutes apart. `dart:io`'s WebSocket.pingInterval sends real
    // protocol-level ping frames on its own schedule regardless, which every conformant WS server
    // (including uvicorn's) answers with a pong transparently -- no backend change needed.
    test('defaults to 20 seconds', () {
      final handler = TaskHandler(WorkspaceRoots(const []));
      final client = HostAgentClient(
        wsUrl: server.wsUrl,
        authToken: 't',
        taskHandler: handler,
        logger: (_) {},
      );
      expect(client.pingInterval, const Duration(seconds: 20));
    });

    test('a custom interval reaches the actual connected socket, not just this class', () async {
      final handler = TaskHandler(WorkspaceRoots(const []));
      final client = HostAgentClient(
        wsUrl: server.wsUrl,
        authToken: 't',
        taskHandler: handler,
        logger: (_) {},
        pingInterval: const Duration(seconds: 7),
      );
      unawaited(client.run());
      addTearDown(client.stop);

      await server.clientSocket;
      await _settle(() => client.socketPingInterval != null);
      expect(client.socketPingInterval, const Duration(seconds: 7));
    });
  });

  group('isConnected', () {
    // `control_server.dart`'s `/status` reports this distinctly from "have a stored token" --
    // the daemon believing itself paired while its actual socket to the backend is down (mid
    // reconnect backoff after a drop) is exactly the bug this field exists to make visible.
    test('is false before the first connection attempt completes', () async {
      final handler = TaskHandler(WorkspaceRoots(const []));
      final client = HostAgentClient(
        wsUrl: server.wsUrl,
        authToken: 't',
        taskHandler: handler,
        logger: (_) {},
      );
      expect(client.isConnected, isFalse);
    });

    test('becomes true once the socket connects', () async {
      final handler = TaskHandler(WorkspaceRoots(const []));
      final client = HostAgentClient(
        wsUrl: server.wsUrl,
        authToken: 't',
        taskHandler: handler,
        logger: (_) {},
      );
      unawaited(client.run());
      addTearDown(client.stop);

      await server.clientSocket;
      // The socket future resolves on the server side the instant it accepts the upgrade,
      // slightly before the client's own `_isConnected = true` assignment runs -- give the
      // client's event loop turn a chance to catch up rather than asserting in the same tick.
      await _settle(() => client.isConnected);
      expect(client.isConnected, isTrue);
    });

    test('becomes false again once the server drops the connection', () async {
      final handler = TaskHandler(WorkspaceRoots(const []));
      final client = HostAgentClient(
        wsUrl: server.wsUrl,
        authToken: 't',
        taskHandler: handler,
        logger: (_) {},
        // A short max backoff so this test doesn't wait out the real 30s cap for the reconnect
        // attempt `run()` makes right after -- irrelevant to what this test asserts either way.
        maxBackoff: const Duration(milliseconds: 50),
      );
      unawaited(client.run());
      addTearDown(client.stop);

      final socket = await server.clientSocket;
      await _settle(() => client.isConnected);
      expect(client.isConnected, isTrue);

      await socket.close();
      await Future<void>.delayed(const Duration(milliseconds: 100));
      expect(client.isConnected, isFalse);
    });

    test('becomes false immediately when stop() is called', () async {
      final handler = TaskHandler(WorkspaceRoots(const []));
      final client = HostAgentClient(
        wsUrl: server.wsUrl,
        authToken: 't',
        taskHandler: handler,
        logger: (_) {},
      );
      unawaited(client.run());

      await server.clientSocket;
      await _settle(() => client.isConnected);
      expect(client.isConnected, isTrue);

      client.stop();
      expect(client.isConnected, isFalse);
    });
  });
}

/// Minimal single-subscriber "pull one at a time" wrapper -- avoids pulling in
/// `package:async` just for `StreamQueue` in a small test file.
class StreamQueue<T> {
  StreamQueue(Stream<T> source) : _iterator = StreamIterator(source);
  final StreamIterator<T> _iterator;

  Future<T> get next async {
    final hasNext = await _iterator.moveNext();
    if (!hasNext) {
      throw StateError('stream closed before the expected frame arrived');
    }
    return _iterator.current;
  }
}
