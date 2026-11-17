// Drives the daemon exactly as `aura_hostd run` wires it -- a real ControlServer over HTTP, a real
// BackendConnection (HostAgentClient + TaskHandler.live) over a WebSocket to a fake backend, and
// one WorkspaceStore on disk between them -- rather than handing a test the same in-memory
// WorkspaceRoots object on both sides, which is what hid audit INT-002: the UI's root actions
// went to disk, and the agent kept resolving against a start-up snapshot.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:aura_hostd/src/approval_queue.dart';
import 'package:aura_hostd/src/backend_connection.dart';
import 'package:aura_hostd/src/config.dart';
import 'package:aura_hostd/src/control_server.dart';
import 'package:aura_hostd/src/debug_session_manager.dart';
import 'package:aura_hostd/src/isolated_task_registry.dart';
import 'package:aura_hostd/src/rpc_client.dart';
import 'package:aura_hostd/src/terminal_session_manager.dart';
import 'package:aura_hostd/src/workspace_store.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

/// A backend stand-in: accepts any number of host-agent WebSocket connections and records the
/// auth token each one presented.
class _FakeBackend {
  _FakeBackend._(this._server);

  final HttpServer _server;
  final List<_Connection> connections = [];
  final _connected = StreamController<_Connection>.broadcast();

  static Future<_FakeBackend> start() async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final backend = _FakeBackend._(server);
    server.listen((request) async {
      final socket = await WebSocketTransformer.upgrade(request);
      final connection = _Connection(socket);
      await connection.ready;
      backend.connections.add(connection);
      backend._connected.add(connection);
    });
    return backend;
  }

  String get url => 'http://127.0.0.1:${_server.port}';

  Future<_Connection> nextConnection() => _connected.stream.first.timeout(const Duration(seconds: 10));

  Future<void> close() async {
    for (final c in connections) {
      await c.socket.close();
    }
    await _server.close(force: true);
  }
}

class _Connection {
  _Connection(this.socket) {
    socket.listen((message) {
      final frame = jsonDecode(message as String) as Map<String, dynamic>;
      if (frame['type'] == 'auth') {
        authToken = frame['token'] as String?;
        if (!_ready.isCompleted) _ready.complete();
        return;
      }
      if (frame['type'] == 'stream') return;
      final id = frame['id'] as String;
      _replies.remove(id)?.complete(frame);
    }, onDone: () {
      for (final c in _replies.values) {
        if (!c.isCompleted) c.completeError(StateError('socket closed'));
      }
    });
  }

  final WebSocket socket;
  String? authToken;
  final _ready = Completer<void>();
  final Map<String, Completer<Map<String, dynamic>>> _replies = {};
  var _next = 0;

  Future<void> get ready => _ready.future;

  /// Sends a `call` frame and returns its `result`/`error` reply frame.
  Future<Map<String, dynamic>> call(String method, Map<String, Object?> params) {
    final id = 'c${_next++}';
    final completer = Completer<Map<String, dynamic>>();
    _replies[id] = completer;
    socket.add(jsonEncode({'type': 'call', 'id': id, 'method': method, 'params': params}));
    return completer.future.timeout(const Duration(seconds: 10));
  }

  /// Sends a call without waiting for its reply.
  void callAndForget(String method, Map<String, Object?> params) {
    socket.add(jsonEncode({'type': 'call', 'id': 'f${_next++}', 'method': method, 'params': params}));
  }
}

class _Control {
  _Control(this.port, this.token);

  final int port;
  final String token;
  final HttpClient _client = HttpClient();

  Future<(int, Map<String, dynamic>)> request(String method, String path, {Map<String, Object?>? body}) async {
    final request = await _client.openUrl(method, Uri.parse('http://127.0.0.1:$port$path'));
    request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
    if (body != null) {
      request.headers.contentType = ContentType.json;
      request.write(jsonEncode(body));
    }
    final response = await request.close();
    final raw = await utf8.decoder.bind(response).join();
    return (response.statusCode, raw.isEmpty ? <String, dynamic>{} : jsonDecode(raw) as Map<String, dynamic>);
  }

  void close() => _client.close(force: true);
}

Future<void> _until(bool Function() condition) async {
  final deadline = DateTime.now().add(const Duration(seconds: 10));
  while (!condition()) {
    if (DateTime.now().isAfter(deadline)) throw TimeoutException('condition never became true');
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }
}

void main() {
  late Directory tempDir;
  late Directory configDir;
  late String rootA;
  late String rootB;
  late WorkspaceStore workspaceStore;
  late ConfigStore configStore;
  late ApprovalQueue approvals;
  late BackendConnection backend;
  late ControlServer controlServer;
  late _FakeBackend fakeBackend;
  late _Control control;

  setUp(() async {
    tempDir = Directory.systemTemp.createTempSync('aura_hostd_wiring_test_');
    configDir = Directory(p.join(tempDir.path, 'config'))..createSync();
    final dirA = Directory(p.join(tempDir.path, 'a'))..createSync();
    final dirB = Directory(p.join(tempDir.path, 'b'))..createSync();
    File(p.join(dirA.path, 'secret.txt')).writeAsStringSync('in a');
    File(p.join(dirB.path, 'other.txt')).writeAsStringSync('in b');
    rootA = dirA.resolveSymbolicLinksSync();
    rootB = dirB.resolveSymbolicLinksSync();

    fakeBackend = await _FakeBackend.start();
    workspaceStore = WorkspaceStore.forConfigDir(configDir);
    configStore = ConfigStore.forConfigDir(configDir);
    configStore.save(DaemonConfig(backendUrl: fakeBackend.url, authToken: 'tok-1'));
    final initial = workspaceStore.load()..addRoot(rootA, trusted: true);
    workspaceStore.save(initial);

    // The same construction order and arguments as bin/aura_hostd.dart's _runDaemon.
    approvals = ApprovalQueue();
    final isolatedTasks = IsolatedTaskRegistry(workspaceStore.live);
    backend = BackendConnection(
      configStore: configStore,
      workspaceStore: workspaceStore,
      approvalQueue: approvals,
      isolatedTasks: isolatedTasks,
      logger: (_) {},
      clientFactory: ({required wsUrl, required authToken, required taskHandler}) => HostAgentClient(
        wsUrl: wsUrl,
        authToken: authToken,
        taskHandler: taskHandler,
        logger: (_) {},
        minBackoff: const Duration(milliseconds: 20),
      ),
    );
    controlServer = ControlServer(
      workspaceStore: workspaceStore,
      configStore: configStore,
      onPaired: (_) => backend.restart(),
      terminalSessions: TerminalSessionManager(workspaceStore),
      debugSessions: DebugSessionManager(workspaceStore),
      approvalQueue: approvals,
      isolatedTasks: isolatedTasks,
      isBackendConnected: () => backend.isConnected,
      isCredentialsRejected: () => backend.credentialsRejected,
      lastCloseCode: () => backend.lastCloseCode,
      logger: (_) {},
    );
    await controlServer.start(configDir);
    final controlFile =
        jsonDecode(File(p.join(configDir.path, 'control.json')).readAsStringSync()) as Map<String, dynamic>;
    control = _Control(controlFile['port'] as int, controlFile['token'] as String);
  });

  tearDown(() async {
    backend.stop();
    control.close();
    await controlServer.stop();
    await fakeBackend.close();
    tempDir.deleteSync(recursive: true);
  });

  Future<_Connection> connect() async {
    final next = fakeBackend.nextConnection();
    backend.startIfAuthenticated();
    return next;
  }

  group('INT-002: root actions through the control server reach the agent at once', () {
    test('a revoked root refuses read_file', () async {
      final conn = await connect();
      final before = await conn.call('read_file', {'path': p.join(rootA, 'secret.txt')});
      expect(before['type'], 'result');

      final (status, _) = await control.request('POST', '/roots', body: {'action': 'revoke', 'path': rootA});
      expect(status, 200);

      final after = await conn.call('read_file', {'path': p.join(rootA, 'secret.txt')});
      expect(after['type'], 'error');
    });

    test('a task bound to a root at create_task is refused once that root is revoked', () async {
      final conn = await connect();
      final created = await conn.call('create_task', {'cwd': rootA});
      final taskId = (created['result'] as Map)['task_id'];

      await control.request('POST', '/roots', body: {'action': 'revoke', 'path': rootA});

      final read = await conn.call('read_file', {'path': 'secret.txt', 'task_id': taskId});
      expect(read['type'], 'error');
      expect(read['message'], contains('revoked'));
      final ran = await conn.call('run', {
        'command': ['sh', '-c', 'touch ran.txt'],
        'task_id': taskId,
        'timeout_s': 5,
      });
      expect(ran['type'], 'error');
      expect(File(p.join(rootA, 'ran.txt')).existsSync(), isFalse);
    });

    test('untrusting a root puts writes on a bound task back behind approval', () async {
      final conn = await connect();
      final created = await conn.call('create_task', {'cwd': rootA});
      final taskId = (created['result'] as Map)['task_id'];

      final (status, _) = await control.request('POST', '/roots', body: {'action': 'untrust', 'path': rootA});
      expect(status, 200);

      conn.callAndForget('write_file', {'path': 'new.txt', 'content': 'x', 'task_id': taskId});
      await _until(() => approvals.list().isNotEmpty);
      expect(File(p.join(rootA, 'new.txt')).existsSync(), isFalse);
    });

    test('a root granted after start-up can be used by create_task', () async {
      final conn = await connect();
      final (status, _) = await control.request('POST', '/roots', body: {'action': 'grant', 'path': rootB});
      expect(status, 200);

      final created = await conn.call('create_task', {'cwd': rootB});
      expect(created['type'], 'result');
      final read = await conn.call('read_file', {'path': p.join(rootB, 'other.txt')});
      expect((read['result'] as Map)['content'], 'in b');
    });

    test('a revoke made by another process (the CLI) is seen on the next RPC', () async {
      final conn = await connect();
      // What `aura_hostd revoke` does: its own store instance, its own load/save.
      final cli = WorkspaceStore.forConfigDir(configDir);
      final roots = cli.load()..removeRoot(rootA);
      cli.save(roots);

      final read = await conn.call('read_file', {'path': p.join(rootA, 'secret.txt')});
      expect(read['type'], 'error');
    });
  });

  group('INT-003: a 4401 rejection is visible in /status and a re-pair reconnects', () {
    test('status reports credentials_rejected, and a new token connects again', () async {
      final first = await connect();
      expect(first.authToken, 'tok-1');
      await first.socket.close(4401);
      await _until(() => backend.credentialsRejected);

      final (status, body) = await control.request('GET', '/status');
      expect(status, 200);
      expect(body['authenticated'], true);
      expect(body['backend_connected'], false);
      expect(body['credentials_rejected'], true);
      expect(body['last_close_code'], 4401);

      // What a completed /pair/status does: save the new token, then onPaired.
      final next = fakeBackend.nextConnection();
      configStore.save(DaemonConfig(backendUrl: fakeBackend.url, authToken: 'tok-2'));
      backend.restart();
      final second = await next;
      expect(second.authToken, 'tok-2');
      await _until(() => backend.isConnected);

      final (_, after) = await control.request('GET', '/status');
      expect(after['credentials_rejected'], false);
      expect(after['backend_connected'], true);
    });

    test('startIfAuthenticated alone also reconnects after a rejection', () async {
      final first = await connect();
      await first.socket.close(4401);
      await _until(() => backend.credentialsRejected);

      configStore.save(DaemonConfig(backendUrl: fakeBackend.url, authToken: 'tok-3'));
      final second = await connect();
      expect(second.authToken, 'tok-3');
    });
  });

  group('WP8: remote control is decided on this computer, over the real socket', () {
    test('a remote task is refused until the desktop turns remote control on', () async {
      final conn = await connect();
      final refused = await conn.call('create_task', {'origin': 'remote', 'cwd': rootA});
      expect(refused['type'], 'error');
      expect(refused['message'], startsWith('remote_control_disabled: '));

      final (status, _) = await control.request('POST', '/remote_control', body: {'enabled': true});
      expect(status, 200);
      final created = await conn.call('create_task', {'origin': 'remote', 'cwd': rootA});
      expect(created['type'], 'result');
    });

    test('a remote task in a trusted folder needs confirmation, never the desktop queue, and '
        'stops at the next call once remote control is turned off', () async {
      final conn = await connect();
      await control.request('POST', '/remote_control', body: {'enabled': true});
      final created = await conn.call('create_task', {'origin': 'remote', 'cwd': rootA});
      final taskId = (created['result'] as Map)['task_id'];

      final unconfirmed = await conn.call('run', {
        'command': ['sh', '-c', 'mkdir unconfirmed'],
        'task_id': taskId,
        'timeout_s': 5,
      });
      expect(unconfirmed['type'], 'error');
      expect(unconfirmed['message'], startsWith('confirmation_required: '));
      expect(approvals.list(), isEmpty);
      expect(Directory(p.join(rootA, 'unconfirmed')).existsSync(), isFalse);

      final confirmed = await conn.call('run', {
        'command': ['sh', '-c', 'mkdir confirmed'],
        'task_id': taskId,
        'timeout_s': 5,
        'confirmed': true,
      });
      expect(confirmed['type'], 'result');
      expect(Directory(p.join(rootA, 'confirmed')).existsSync(), isTrue);

      await control.request('POST', '/remote_control', body: {'enabled': false});
      final after = await conn.call('write_file', {
        'path': 'after.txt',
        'content': 'x',
        'task_id': taskId,
        'confirmed': true,
      });
      expect(after['type'], 'error');
      expect(after['message'], startsWith('remote_control_disabled: '));
      expect(File(p.join(rootA, 'after.txt')).existsSync(), isFalse);
    });

    test('list_roots answers with the live folders and flag, with no task', () async {
      final conn = await connect();
      final before = await conn.call('list_roots', {});
      expect(before['type'], 'result');
      expect(before['result'], {
        'roots': [
          {'path': rootA, 'trusted': true},
        ],
        'remote_control': false,
      });

      await control.request('POST', '/roots', body: {'action': 'grant', 'path': rootB});
      await control.request('POST', '/remote_control', body: {'enabled': true});
      final after = await conn.call('list_roots', {});
      expect(after['result'], {
        'roots': [
          {'path': rootA, 'trusted': true},
          {'path': rootB, 'trusted': false},
        ],
        'remote_control': true,
      });
    });
  });

  test('INT-007: a dropped connection cancels approvals its calls were waiting on', () async {
    final conn = await connect();
    await control.request('POST', '/roots', body: {'action': 'untrust', 'path': rootA});
    conn.callAndForget('run', {
      'command': ['sh', '-c', 'mkdir made-after-drop'],
      'cwd': rootA,
      'timeout_s': 5,
    });
    await _until(() => approvals.list().isNotEmpty);
    final pendingId = approvals.list().single.id;

    await conn.socket.close();
    await _until(() => approvals.list().isEmpty);

    // A human answering the (now cancelled) approval must not run anything.
    approvals.respond(pendingId, true);
    await Future<void>.delayed(const Duration(milliseconds: 200));
    expect(Directory(p.join(rootA, 'made-after-drop')).existsSync(), isFalse);
  });
}
