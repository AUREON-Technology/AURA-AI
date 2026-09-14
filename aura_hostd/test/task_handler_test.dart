import 'dart:io';

import 'package:aura_hostd/src/approval_queue.dart';
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

    test('reports output via onChunk, matching the aggregated result exactly', () async {
      roots.setTrusted(workspaceDir.path, true);
      final chunks = <MapEntry<String, String>>[];
      final result = await handler.run(
        {
          'command': ['sh', '-c', 'echo out1; echo err1 1>&2; echo out2'],
          'timeout_s': 5.0,
        },
        onChunk: (stream, data) => chunks.add(MapEntry(stream, data)),
      );

      expect(result['exit_code'], 0);
      final stdoutFromChunks =
          chunks.where((e) => e.key == 'stdout').map((e) => e.value).join();
      final stderrFromChunks =
          chunks.where((e) => e.key == 'stderr').map((e) => e.value).join();
      expect(stdoutFromChunks, result['stdout']);
      expect(stderrFromChunks, result['stderr']);
      expect(stdoutFromChunks, contains('out1'));
      expect(stdoutFromChunks, contains('out2'));
      expect(stderrFromChunks, contains('err1'));
    }, testOn: '!windows');

    test('run works identically with no onChunk callback at all', () async {
      roots.setTrusted(workspaceDir.path, true);
      final result = await handler.run({
        'command': ['sh', '-c', 'echo hi'],
        'timeout_s': 5.0,
      });
      expect(result['exit_code'], 0);
      expect(result['stdout'], contains('hi'));
    }, testOn: '!windows');

    test('caps captured/forwarded output at the daemon-side limit', () async {
      roots.setTrusted(workspaceDir.path, true);
      final result = await handler.run({
        'command': ['sh', '-c', 'yes x | head -c 600000'],
        'timeout_s': 10.0,
      });
      expect(result['exit_code'], 0);
      expect((result['stdout'] as String).length, lessThanOrEqualTo(500000));
      expect(result['stderr'], contains('truncated'));
    }, testOn: '!windows');
  });

  group('needsApproval consent tier (D5)', () {
    late ApprovalQueue approvals;

    setUp(() {
      approvals = ApprovalQueue();
      handler = TaskHandler(roots, approvals);
    });

    test(
        'a non-allowlisted command in an untrusted workspace queues for approval instead of '
        'running immediately, then runs once approved', () async {
      final future = handler.run({
        'command': ['sh', '-c', 'echo hello > written.txt'],
        'timeout_s': 5.0,
      });
      // `run()` executes synchronously up to the first real suspension point -- the queue
      // registration below has already happened by the time `run()` returns a Future.
      expect(approvals.list(), hasLength(1));
      expect(File(p.join(workspaceDir.path, 'written.txt')).existsSync(), isFalse);

      approvals.respond(approvals.list().single.id, true);
      final result = await future;
      expect(result['exit_code'], 0);
      expect(File(p.join(workspaceDir.path, 'written.txt')).existsSync(), isTrue);
    }, testOn: '!windows');

    test('rejecting a queued command throws and never runs it', () async {
      final future = handler.run({
        'command': ['sh', '-c', 'echo hello > written.txt'],
        'timeout_s': 5.0,
      });
      approvals.respond(approvals.list().single.id, false);
      await expectLater(future, throwsA(isA<RpcError>()));
      expect(File(p.join(workspaceDir.path, 'written.txt')).existsSync(), isFalse);
    }, testOn: '!windows');

    test(
        'an approved command is refused if its workspace root was revoked while the approval '
        'was pending (post-approval revalidation)', () async {
      final future = handler.run({
        'command': ['sh', '-c', 'echo hello > written.txt'],
        'timeout_s': 5.0,
      });
      final pending = approvals.list().single;
      roots.removeRoot(workspaceDir.path);
      approvals.respond(pending.id, true);
      await expectLater(future, throwsA(isA<RpcError>()));
      expect(File(p.join(workspaceDir.path, 'written.txt')).existsSync(), isFalse);
    }, testOn: '!windows');

    test('a trusted workspace never queues -- the command runs immediately', () async {
      roots.setTrusted(workspaceDir.path, true);
      final result = await handler.run({
        'command': ['sh', '-c', 'echo hello > written.txt'],
        'timeout_s': 5.0,
      });
      expect(result['exit_code'], 0);
      expect(approvals.list(), isEmpty);
    }, testOn: '!windows');
  });
}
