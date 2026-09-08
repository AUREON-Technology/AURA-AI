import 'dart:io';

import 'package:aura_hostd/src/containment.dart';
import 'package:aura_hostd/src/task_handler.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  late Directory tempDir;
  late Directory workspaceDir;
  late WorkspaceRoots roots;
  late TaskHandler handler;

  setUp(() {
    tempDir = Directory.systemTemp.createTempSync('aura_hostd_task_test_');
    workspaceDir = Directory(p.join(tempDir.path, 'workspace'))..createSync();
    roots = WorkspaceRoots(const []);
    roots.addRoot(workspaceDir.path);
    handler = TaskHandler(roots);
  });

  tearDown(() {
    tempDir.deleteSync(recursive: true);
  });

  test('createTask returns a fresh, non-empty task_id each time', () async {
    final a = await handler.createTask(const {});
    final b = await handler.createTask(const {});
    expect(a['task_id'], isNotEmpty);
    expect(b['task_id'], isNotEmpty);
    expect(a['task_id'], isNot(b['task_id']));
  });

  group('write_file / read_file', () {
    test('writes and reads back a file inside the granted root', () async {
      await handler.writeFile({'path': 'notes.txt', 'content': 'hello'});
      final result = await handler.readFile({'path': 'notes.txt'});
      expect(result['content'], 'hello');
    });

    test('creates parent directories as needed', () async {
      await handler.writeFile({'path': 'src/lib/main.dart', 'content': 'void main() {}'});
      expect(File(p.join(workspaceDir.path, 'src', 'lib', 'main.dart')).existsSync(), isTrue);
    });

    test('refuses to read a path outside every granted root', () async {
      final outside = Directory(p.join(tempDir.path, 'outside'))..createSync();
      final secret = File(p.join(outside.path, 'secret.txt'))..writeAsStringSync('nope');
      expect(
        () => handler.readFile({'path': secret.path}),
        throwsA(isA<RpcError>()),
      );
    });

    test('refuses to write outside every granted root even via ".."', () async {
      expect(
        () => handler.writeFile({'path': '../escape.txt', 'content': 'x'}),
        throwsA(isA<RpcError>()),
      );
      expect(File(p.join(tempDir.path, 'escape.txt')).existsSync(), isFalse);
    });

    test('read_file reports a clear error for a missing file', () async {
      expect(
        () => handler.readFile({'path': 'does_not_exist.txt'}),
        throwsA(isA<RpcError>()),
      );
    });
  });

  group('run', () {
    test('runs pwd (allowlisted) successfully in an untrusted workspace', () async {
      final result = await handler.run({
        'command': ['sh', '-c', 'pwd'],
        'timeout_s': 5.0,
      });
      expect(result['exit_code'], 0);
      expect((result['stdout'] as String).trim(), workspaceDir.path);
    }, testOn: '!windows');

    test('refuses a non-allowlisted command in an untrusted workspace without running it',
        () async {
      expect(
        () => handler.run({
          'command': ['sh', '-c', 'echo hello > written.txt'],
          'timeout_s': 5.0,
        }),
        throwsA(isA<RpcError>()),
      );
      expect(File(p.join(workspaceDir.path, 'written.txt')).existsSync(), isFalse);
    }, testOn: '!windows');

    test('allows an arbitrary command once the workspace is trusted', () async {
      roots.setTrusted(workspaceDir.path, true);
      final result = await handler.run({
        'command': ['sh', '-c', 'echo hello > written.txt'],
        'timeout_s': 5.0,
      });
      expect(result['exit_code'], 0);
      expect(File(p.join(workspaceDir.path, 'written.txt')).existsSync(), isTrue);
    }, testOn: '!windows');

    test('refuses rm -rf even in a trusted workspace', () async {
      roots.setTrusted(workspaceDir.path, true);
      expect(
        () => handler.run({
          'command': ['sh', '-c', 'rm -rf .'],
          'timeout_s': 5.0,
        }),
        throwsA(isA<RpcError>()),
      );
    }, testOn: '!windows');

    test('kills a command that exceeds its timeout', () async {
      roots.setTrusted(workspaceDir.path, true);
      final result = await handler.run({
        'command': ['sh', '-c', 'sleep 30'],
        'timeout_s': 0.5,
      });
      expect(result['exit_code'], isNot(0));
      expect(result['stderr'], contains('timeout'));
    }, testOn: '!windows');

    test('refuses to run with no granted workspace root', () async {
      final emptyHandler = TaskHandler(WorkspaceRoots(const []));
      expect(
        () => emptyHandler.run({
          'command': ['sh', '-c', 'pwd'],
          'timeout_s': 5.0,
        }),
        throwsA(isA<RpcError>()),
      );
    });
  });
}
