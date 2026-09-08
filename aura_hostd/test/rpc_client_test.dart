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
