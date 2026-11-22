// The `/terminal` WebSocket protocol, end to end through the real [TerminalSessionManager] over a
// real loopback WebSocket, with a fake PTY standing in for the shell (the OS backends have their own
// tests: posix_pty_session_test.dart; ConPTY needs a real interactive Windows session).
//
// Pins the decisions in docs/module-readiness/desktop-ide/terminal/2026-11-22/ -- notably D-2: a live
// terminal survives its root being untrusted, because the trust gate decides who may *start* a
// shell, not who may keep the user's own one.
import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:aura_hostd/src/containment.dart';
import 'package:aura_hostd/src/error_codes.dart';
import 'package:aura_hostd/src/pty_session_interface.dart';
import 'package:aura_hostd/src/terminal_session_manager.dart';
import 'package:aura_hostd/src/workspace_store.dart';
import 'package:test/test.dart';

class FakePty implements PtySessionInterface {
  FakePty({
    required this.sessionId,
    required this.workingDirectory,
    required this.cols,
    required this.rows,
    this.startError,
  });

  @override
  final String sessionId;
  @override
  final String workingDirectory;
  final int cols;
  final int rows;
  final Object? startError;

  final _output = StreamController<Uint8List>.broadcast();
  final _exit = Completer<int>();
  final List<List<int>> writes = [];
  final List<(int, int)> resizes = [];
  bool started = false;
  bool disposed = false;

  @override
  Stream<Uint8List> get output => _output.stream;
  @override
  Future<int> get exitCode => _exit.future;
  @override
  bool get isChildAlive => started && !disposed && !_exit.isCompleted;
  @override
  int get pid => 4242;

  @override
  Future<void> start() async {
    if (startError != null) throw startError!;
    started = true;
  }

  @override
  int write(Uint8List bytes) {
    writes.add(List<int>.of(bytes));
    return bytes.length;
  }

  @override
  void resize(int cols, int rows) => resizes.add((cols, rows));

  @override
  Future<void> dispose() async {
    disposed = true;
    if (!_output.isClosed) await _output.close();
  }

  void emit(String text) => _output.add(Uint8List.fromList(utf8.encode(text)));
  void finish(int code) => _exit.complete(code);
}

class _Conn {
  _Conn(this.ws) {
    ws.listen(
      (data) {
        if (data is String) frames.add(jsonDecode(data) as Map<String, dynamic>);
      },
      onDone: () => closed = true,
    );
  }

  final WebSocket ws;
  final List<Map<String, dynamic>> frames = [];
  bool closed = false;

  void send(Map<String, Object?> frame) => ws.add(jsonEncode(frame));
  void input(String text) => send({'type': 'input', 'data': base64Encode(utf8.encode(text))});

  List<Map<String, dynamic>> ofType(String type) => frames.where((f) => f['type'] == type).toList();
  String output() => ofType('output').map((f) => utf8.decode(base64Decode(f['data'] as String))).join();
}

Future<void> until(bool Function() condition, String what) async {
  final end = DateTime.now().add(const Duration(seconds: 5));
  while (!condition()) {
    if (DateTime.now().isAfter(end)) fail('timed out waiting for $what');
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }
}

void main() {
  late Directory tmp;
  late WorkspaceStore store;
  late String rootPath;
  late HttpServer server;
  late List<FakePty> ptys;
  late List<String> logs;
  Object? nextStartError;

  Future<_Conn> connect({String? root}) async {
    final ws = await WebSocket.connect(
      'ws://127.0.0.1:${server.port}/terminal?root=${Uri.encodeQueryComponent(root ?? rootPath)}',
    );
    return _Conn(ws);
  }

  void grant({required bool trusted}) {
    final roots = WorkspaceRoots(const []);
    rootPath = roots.addRoot(tmp.path, trusted: trusted).canonicalPath;
    store.save(roots);
  }

  setUp(() async {
    tmp = Directory.systemTemp.createTempSync('aura_term_');
    store = WorkspaceStore.forConfigDir(Directory('${tmp.path}${Platform.pathSeparator}cfg'));
    ptys = [];
    logs = [];
    nextStartError = null;
    grant(trusted: true);

    final manager = TerminalSessionManager(
      store,
      logger: logs.add,
      ptyFactory: ({required sessionId, required workingDirectory, required cols, required rows}) {
        final startError = nextStartError;
        nextStartError = null;
        final pty = FakePty(
          sessionId: sessionId,
          workingDirectory: workingDirectory,
          cols: cols,
          rows: rows,
          startError: startError,
        );
        ptys.add(pty);
        return pty;
      },
    );
    server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((request) async {
      final socket = await WebSocketTransformer.upgrade(request);
      unawaited(manager.handleConnection(socket, rootPath: request.uri.queryParameters['root']!));
    });
  });

  tearDown(() async {
    await server.close(force: true);
    try {
      tmp.deleteSync(recursive: true);
    } on FileSystemException {
      // A handle still open on Windows -- the OS temp cleaner takes it.
    }
  });

  group('trust gate', () {
    test('a root that was never granted is refused with root_not_granted and the socket closes', () async {
      final conn = await connect(root: '${tmp.path}${Platform.pathSeparator}elsewhere');
      await until(() => conn.closed, 'the socket to close');

      expect(conn.ofType('error').single['code'], HostdErrorCode.rootNotGranted);
      expect(ptys, isEmpty);
    });

    test('a granted but untrusted root is refused with workspace_not_trusted', () async {
      grant(trusted: false);
      final conn = await connect();
      await until(() => conn.closed, 'the socket to close');

      expect(conn.ofType('error').single['code'], HostdErrorCode.workspaceNotTrusted);
      expect(ptys, isEmpty);
    });
  });

  group('session lifecycle', () {
    test('start spawns a PTY in the root with the requested size and answers with started', () async {
      final conn = await connect();
      conn.send({'type': 'start', 'cols': 120, 'rows': 30});
      await until(() => conn.ofType('started').isNotEmpty, 'started');

      final pty = ptys.single;
      expect(pty.started, isTrue);
      expect(pty.workingDirectory, rootPath);
      expect((pty.cols, pty.rows), (120, 30));
      final started = conn.ofType('started').single;
      expect(started['pid'], 4242);
      expect(started['session_id'], pty.sessionId);
    });

    test('a start frame without a size falls back to 80x24', () async {
      final conn = await connect();
      conn.send({'type': 'start'});
      await until(() => ptys.isNotEmpty && ptys.single.started, 'the PTY to start');

      expect((ptys.single.cols, ptys.single.rows), (80, 24));
    });

    test('a second start on the same connection is ignored: one connection, one session', () async {
      final conn = await connect();
      conn.send({'type': 'start', 'cols': 80, 'rows': 24});
      conn.send({'type': 'start', 'cols': 100, 'rows': 40});
      await until(() => conn.ofType('started').isNotEmpty, 'started');
      await Future<void>.delayed(const Duration(milliseconds: 100));

      expect(ptys, hasLength(1));
      expect(conn.ofType('started'), hasLength(1));
    });

    test('a start that fails reports terminal_start_failed, keeps nothing, and the connection can retry', () async {
      nextStartError = PtyException('no pty');
      final conn = await connect();
      conn.send({'type': 'start', 'cols': 80, 'rows': 24});
      await until(() => conn.ofType('error').isNotEmpty, 'the error frame');

      final error = conn.ofType('error').single;
      expect(error['code'], HostdErrorCode.terminalStartFailed);
      expect(error['detail'], contains('no pty'));
      expect(conn.ofType('started'), isEmpty);
      expect(logs.any((l) => l.contains('failed to start')), isTrue);

      conn.send({'type': 'start', 'cols': 80, 'rows': 24});
      await until(() => conn.ofType('started').isNotEmpty, 'started after the retry');
      expect(ptys, hasLength(2));
    });
  });

  group('data flow', () {
    test('input frames reach the PTY as the bytes the client sent', () async {
      final conn = await connect();
      conn.send({'type': 'start', 'cols': 80, 'rows': 24});
      await until(() => conn.ofType('started').isNotEmpty, 'started');

      conn.input('echo héllo مرحبا\r');
      await until(() => ptys.single.writes.isNotEmpty, 'the write');

      expect(utf8.decode(ptys.single.writes.single), 'echo héllo مرحبا\r');
    });

    test('PTY output is forwarded as base64 output frames, in order', () async {
      final conn = await connect();
      conn.send({'type': 'start', 'cols': 80, 'rows': 24});
      await until(() => conn.ofType('started').isNotEmpty, 'started');

      ptys.single.emit('one ');
      ptys.single.emit('two');
      await until(() => conn.output() == 'one two', 'both chunks');
    });

    test('resize after start reaches the PTY; a resize before start is dropped without breaking the session', () async {
      final conn = await connect();
      conn.send({'type': 'resize', 'cols': 200, 'rows': 50});
      conn.send({'type': 'start', 'cols': 80, 'rows': 24});
      await until(() => conn.ofType('started').isNotEmpty, 'started');
      // The early resize is gone: the session was created at the start frame's size.
      expect(ptys.single.resizes, isEmpty);
      expect((ptys.single.cols, ptys.single.rows), (80, 24));

      conn.send({'type': 'resize', 'cols': 132, 'rows': 43});
      await until(() => ptys.single.resizes.isNotEmpty, 'the resize');
      expect(ptys.single.resizes.single, (132, 43));
    });

    test('malformed and binary frames are ignored and do not end the session', () async {
      final conn = await connect();
      conn.ws.add('this is not json');
      conn.ws.add(Uint8List.fromList([1, 2, 3]));
      conn.send({'type': 'start', 'cols': 80, 'rows': 24});
      await until(() => conn.ofType('started').isNotEmpty, 'started');

      conn.input('ls\r');
      await until(() => ptys.single.writes.isNotEmpty, 'the write');
      expect(conn.closed, isFalse);
    });
  });

  group('ending a session', () {
    test('a shell that exits on its own is reported with its exit code', () async {
      final conn = await connect();
      conn.send({'type': 'start', 'cols': 80, 'rows': 24});
      await until(() => conn.ofType('started').isNotEmpty, 'started');

      ptys.single.finish(7);
      await until(() => conn.ofType('exited').isNotEmpty, 'the exited frame');
      expect(conn.ofType('exited').single['exit_code'], 7);
    });

    test('a close frame disposes the PTY and closes the socket', () async {
      final conn = await connect();
      conn.send({'type': 'start', 'cols': 80, 'rows': 24});
      await until(() => conn.ofType('started').isNotEmpty, 'started');

      conn.send({'type': 'close'});
      await until(() => ptys.single.disposed && conn.closed, 'dispose and close');
    });

    test('a dropped connection (no close frame) still disposes the PTY: a session never outlives its socket', () async {
      final conn = await connect();
      conn.send({'type': 'start', 'cols': 80, 'rows': 24});
      await until(() => conn.ofType('started').isNotEmpty, 'started');

      await conn.ws.close();
      await until(() => ptys.single.disposed, 'the PTY to be disposed');
    });

    test('D-2: untrusting the root does not end a live terminal', () async {
      final conn = await connect();
      conn.send({'type': 'start', 'cols': 80, 'rows': 24});
      await until(() => conn.ofType('started').isNotEmpty, 'started');

      final roots = store.load()..setTrusted(rootPath, false);
      store.save(roots);

      conn.input('still here\r');
      await until(() => ptys.single.writes.isNotEmpty, 'the write after untrust');
      expect(ptys.single.disposed, isFalse);
      expect(conn.closed, isFalse);

      // ...but a *new* terminal for that root is refused.
      final second = await connect();
      await until(() => second.closed, 'the second socket to close');
      expect(second.ofType('error').single['code'], HostdErrorCode.workspaceNotTrusted);
    });
  });
}
