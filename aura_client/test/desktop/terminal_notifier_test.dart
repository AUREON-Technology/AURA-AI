// TerminalNotifier (desktop IDE terminal state) against a fake daemon connection, an in-memory
// database and the real EncryptionService. The daemon side of the same protocol is
// aura_hostd/test/terminal_session_manager_test.dart; the wire client is
// test/core/hostd/hostd_terminal_client_io_test.dart.
//
// Covers terminal readiness WP1 (lifecycle) and WP3 (scrollback sealed at rest, D-1):
// docs/module-readiness/desktop-ide/terminal/2026-11-22/.
import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:aura_client/core/database/database.dart';
// The conditional export the notifier itself imports, so the fake is the same type the notifier's
// `clientFactory` expects on every platform the analyzer resolves it for.
import 'package:aura_client/core/hostd/hostd_terminal_client.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:drift/native.dart';
import 'package:encrypt/encrypt.dart' as encrypt;
import 'package:flutter_test/flutter_test.dart';

const _marker = 'SECRET-MARKER-7f3a9c';

class FakeTerminalClient extends HostdTerminalClient {
  FakeTerminalClient({this.connectError});

  final Object? connectError;
  final _out = StreamController<Uint8List>.broadcast();
  final _exit = StreamController<int>.broadcast();
  final _err = StreamController<String>.broadcast();

  String? connectedRoot;
  int? connectedCols;
  int? connectedRows;
  final List<Uint8List> sent = [];
  final List<(int, int)> resizes = [];
  bool disposed = false;

  @override
  Stream<Uint8List> get output => _out.stream;
  @override
  Stream<int> get exitCode => _exit.stream;
  @override
  Stream<String> get errors => _err.stream;

  @override
  Future<void> connect({required String rootPath, required int cols, required int rows}) async {
    if (connectError != null) throw connectError!;
    connectedRoot = rootPath;
    connectedCols = cols;
    connectedRows = rows;
  }

  @override
  void sendInput(Uint8List bytes) => sent.add(bytes);

  @override
  void resize(int cols, int rows) => resizes.add((cols, rows));

  @override
  Future<void> dispose() async {
    disposed = true;
    await _out.close();
    await _exit.close();
    await _err.close();
  }

  void emit(String text) => _out.add(Uint8List.fromList(utf8.encode(text)));
  void exitWith(int code) => _exit.add(code);
  void fail(String message) => _err.add(message);
}

Future<void> until(FutureOr<bool> Function() condition, String what) async {
  final end = DateTime.now().add(const Duration(seconds: 5));
  while (!await condition()) {
    if (DateTime.now().isAfter(end)) fail('timed out waiting for $what');
    await Future<void>.delayed(const Duration(milliseconds: 10));
  }
}

void main() {
  late AppDatabase db;
  late IdeEventBus bus;
  late List<FakeTerminalClient> clients;
  late List<IdeEvent> events;
  late TerminalNotifier notifier;
  Object? nextConnectError;

  TerminalNotifier build({IdeEventBus? onBus}) => TerminalNotifier(
        db,
        onBus ?? bus,
        clientFactory: () {
          final client = FakeTerminalClient(connectError: nextConnectError);
          nextConnectError = null;
          clients.add(client);
          return client;
        },
      );

  TerminalSessionState sessionOf(TerminalNotifier n, String id) => n.state.sessions.singleWhere((s) => s.id == id);

  Future<List<DesktopTerminalStateData>> rows() => db.select(db.desktopTerminalState).get();

  Future<void> insertRow(String workspaceId, String sessionsJson) => db.into(db.desktopTerminalState).insert(
        DesktopTerminalStateCompanion.insert(workspaceId: workspaceId, sessionsJson: sessionsJson),
      );

  String sessionsJsonOf(String id, String output, {int exitCode = 0}) => jsonEncode([
        {'id': id, 'command': null, 'output': output, 'exitCode': exitCode, 'cols': 80, 'rows': 24},
      ]);

  setUp(() {
    EncryptionService.debugSetKeysForTesting(legacy: encrypt.Key.fromSecureRandom(32));
    db = AppDatabase.forTesting(NativeDatabase.memory());
    bus = IdeEventBus();
    clients = [];
    events = [];
    nextConnectError = null;
    bus.stream.listen(events.add);
    notifier = build();
  });

  tearDown(() async {
    notifier.dispose();
    await bus.close();
    await db.close();
    EncryptionService.debugResetKeyForTesting();
  });

  group('lifecycle', () {
    test('start connects to the daemon for the root at the requested size and makes the session active', () async {
      final id = await notifier.start(workspaceId: 'ws', rootPath: '/work/app', cols: 100, rows: 30);
      await pumpEventQueue();

      expect(clients.single.connectedRoot, '/work/app');
      expect((clients.single.connectedCols, clients.single.connectedRows), (100, 30));
      expect(notifier.state.activeSessionId, id);
      final session = sessionOf(notifier, id);
      expect(session.isRunning, isTrue);
      expect(session.workspaceId, 'ws');
      expect(events.whereType<TerminalStarted>().single.sessionId, id);
    });

    test('daemon output lands in the session scrollback and in the emulator', () async {
      final id = await notifier.start(workspaceId: 'ws', rootPath: '/r');
      clients.single.emit('hello world\r\n');
      await pumpEventQueue();

      expect(sessionOf(notifier, id).output, 'hello world\r\n');
      expect(notifier.terminalFor(id)!.buffer.getText(), contains('hello world'));
      expect(events.whereType<TerminalOutput>().single.data, 'hello world\r\n');
    });

    test('an exit code ends the session and is published', () async {
      final id = await notifier.start(workspaceId: 'ws', rootPath: '/r');
      clients.single.exitWith(7);
      await pumpEventQueue();

      final session = sessionOf(notifier, id);
      expect(session.isRunning, isFalse);
      expect(session.exitCode, 7);
      expect(events.whereType<TerminalExited>().single.exitCode, 7);
    });

    test('an error frame is shown in the terminal and ends the session', () async {
      final id = await notifier.start(workspaceId: 'ws', rootPath: '/r');
      clients.single.fail('workspace is not trusted');
      await pumpEventQueue();

      final session = sessionOf(notifier, id);
      expect(session.output, contains('[error] workspace is not trusted'));
      expect(session.isRunning, isFalse);
    });

    test('a daemon that cannot be reached is shown in the terminal instead of throwing', () async {
      nextConnectError = HostdTerminalUnavailable('daemon is not running');
      final id = await notifier.start(workspaceId: 'ws', rootPath: '/r');

      final session = sessionOf(notifier, id);
      expect(session.output, contains('[error] daemon is not running'));
      expect(session.isRunning, isFalse);
      expect(events.whereType<TerminalStarted>(), isEmpty);
    });

    test('typing in the emulator is sent to the daemon as UTF-8 bytes', () async {
      final id = await notifier.start(workspaceId: 'ws', rootPath: '/r');
      notifier.terminalFor(id)!.textInput('ls م');

      expect(utf8.decode(clients.single.sent.single), 'ls م');
    });

    test('resize reaches the daemon and updates the session size', () async {
      final id = await notifier.start(workspaceId: 'ws', rootPath: '/r');
      await pumpEventQueue(); // the emulator's own creation-time resize (80x24) is delivered first
      notifier.resize(id, 120, 40);

      expect(clients.single.resizes.last, (120, 40));
      final session = sessionOf(notifier, id);
      expect((session.cols, session.rows), (120, 40));
    });

    test('restart reconnects an ended session in place, keeping its id and its scrollback', () async {
      final id = await notifier.start(workspaceId: 'ws', rootPath: '/r', cols: 90, rows: 20);
      clients.single.emit('before restart\r\n');
      clients.single.exitWith(1);
      await pumpEventQueue();

      await notifier.restart(id, rootPath: '/r');

      expect(clients, hasLength(2));
      expect(clients.last.connectedRoot, '/r');
      expect((clients.last.connectedCols, clients.last.connectedRows), (90, 20));
      final session = sessionOf(notifier, id);
      expect(session.isRunning, isTrue);
      expect(session.exitCode, isNull);
      expect(notifier.terminalFor(id)!.buffer.getText(), contains('before restart'));
    });

    test('restart does nothing for a session that is still running', () async {
      final id = await notifier.start(workspaceId: 'ws', rootPath: '/r');
      await notifier.restart(id, rootPath: '/r');

      expect(clients, hasLength(1));
    });

    test('closeSession disposes the connection, removes the session and drops its saved row', () async {
      final id = await notifier.start(workspaceId: 'ws', rootPath: '/r');
      clients.single.emit('x\r\n');
      clients.single.exitWith(0);
      await until(() async => (await rows()).isNotEmpty, 'the row to be saved');

      await notifier.closeSession(id);

      expect(clients.single.disposed, isTrue);
      expect(notifier.state.sessions, isEmpty);
      expect(notifier.state.activeSessionId, isNull);
      expect(notifier.terminalFor(id), isNull);
      expect(await rows(), isEmpty);
    });
  });

  group('scrollback is sealed at rest (D-1)', () {
    test('the stored column is an encryption envelope that does not contain the output', () async {
      await notifier.start(workspaceId: 'ws', rootPath: '/r');
      clients.single.emit('API_KEY=$_marker\r\n');
      clients.single.exitWith(0);
      await until(() async => (await rows()).isNotEmpty, 'the row to be saved');

      final raw = (await rows()).single.sessionsJson;
      expect(EncryptionService.isEnvelope(raw), isTrue);
      expect(raw, isNot(contains(_marker)));
      expect(raw, isNot(contains('API_KEY')));
      expect(EncryptionService.decryptText(raw), contains(_marker));
    });

    test('a saved terminal is restored, as an ended session, with its last screen', () async {
      final id = await notifier.start(workspaceId: 'ws', rootPath: '/r', cols: 90, rows: 20);
      clients.single.emit('build finished $_marker\r\n');
      clients.single.exitWith(3);
      await until(() async => (await rows()).isNotEmpty, 'the row to be saved');

      final otherBus = IdeEventBus();
      addTearDown(otherBus.close);
      final restored = build(onBus: otherBus);
      addTearDown(restored.dispose);
      otherBus.publish(const WorkspaceOpened('ws'));
      await until(() => restored.state.sessions.isNotEmpty, 'the session to be restored');

      final session = restored.state.sessions.single;
      expect(session.id, id);
      expect(session.isRunning, isFalse);
      expect(session.exitCode, 3);
      expect((session.cols, session.rows), (90, 20));
      expect(session.output, contains(_marker));
      expect(restored.terminalFor(id)!.buffer.getText(), contains(_marker));
    });

    test('a plaintext row from before the change is never replayed and is deleted', () async {
      await insertRow('ws', sessionsJsonOf('term_old', 'token=$_marker\r\n'));

      bus.publish(const WorkspaceOpened('ws'));
      await until(() async => (await rows()).isEmpty, 'the plaintext row to be deleted');

      expect(notifier.state.sessions, isEmpty);
      expect(notifier.terminalFor('term_old'), isNull);
    });

    test('an envelope this device cannot open is skipped but kept, since the key may arrive', () async {
      final sealedUnderOtherKey = EncryptionService.encryptText(sessionsJsonOf('term_x', 'hi\r\n'));
      EncryptionService.debugSetKeysForTesting(legacy: encrypt.Key.fromSecureRandom(32));
      await insertRow('ws', sealedUnderOtherKey);

      bus.publish(const WorkspaceOpened('ws'));
      await pumpEventQueue();
      await Future<void>.delayed(const Duration(milliseconds: 200));

      expect(notifier.state.sessions, isEmpty);
      expect(await rows(), hasLength(1));
    });

    test('restoring one workspace keeps another workspace\'s live session', () async {
      final live = await notifier.start(workspaceId: 'ws-a', rootPath: '/a');
      await insertRow('ws-b', EncryptionService.encryptText(sessionsJsonOf('term_b', 'b output\r\n')));

      bus.publish(const WorkspaceOpened('ws-b'));
      await until(() => notifier.state.sessions.length == 2, 'ws-b to be restored');

      expect(notifier.state.sessions.map((s) => s.id), containsAll([live, 'term_b']));
      expect(sessionOf(notifier, live).isRunning, isTrue);
    });

    test('with no encryption key nothing is written, and nothing throws', () async {
      final id = await notifier.start(workspaceId: 'ws', rootPath: '/r');
      clients.single.emit('API_KEY=$_marker\r\n');
      await pumpEventQueue();

      EncryptionService.debugResetKeyForTesting();
      clients.single.exitWith(0);
      await pumpEventQueue();
      await Future<void>.delayed(const Duration(milliseconds: 200));

      expect(sessionOf(notifier, id).isRunning, isFalse);
      expect(await rows(), isEmpty);
    });
  });

  group('capScrollback (DATA-001)', () {
    test('text within the cap is returned unchanged', () {
      expect(TerminalNotifier.capScrollback('short\r\n'), 'short\r\n');
    });

    test('over the cap it keeps the tail and starts on a line boundary', () {
      final text = '0123456789\n' * 50000; // 550 000 chars; the raw cut lands mid-line
      final capped = TerminalNotifier.capScrollback(text);

      expect(capped.length, lessThanOrEqualTo(500000));
      expect(capped.length, greaterThan(500000 - 11));
      expect(capped, startsWith('0123456789\n'));
      expect(text, endsWith(capped));
    });

    test('with no newline in the tail it falls back to the exact tail', () {
      final capped = TerminalNotifier.capScrollback('x' * 500005);

      expect(capped.length, 500000);
    });

    test('it never starts on the second half of a surrogate pair', () {
      final text = '\u{1F600}' * 250001 + 'z'; // 500 003 UTF-16 units; the raw cut is index 3, a low surrogate
      final capped = TerminalNotifier.capScrollback(text);

      expect(capped.codeUnitAt(0), isNot(inInclusiveRange(0xDC00, 0xDFFF)));
      expect(capped.runes.every((r) => r != 0xFFFD), isTrue);
      expect(capped, endsWith('z'));
    });
  });
}
