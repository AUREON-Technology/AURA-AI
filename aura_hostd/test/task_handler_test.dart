import 'dart:async';
import 'dart:io';

import 'package:aura_hostd/src/approval_queue.dart';
import 'package:aura_hostd/src/containment.dart';
import 'package:aura_hostd/src/git_service.dart';
import 'package:aura_hostd/src/task_handler.dart';
import 'package:aura_hostd/src/workspace_store.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

Future<ProcessResult> _git(String cwd, List<String> args) =>
    Process.run('git', args, workingDirectory: cwd);

Future<void> _initRepo(String cwd) async {
  await _git(cwd, ['init', '-q', '-b', 'main']);
  await _git(cwd, ['config', 'user.name', 'Test User']);
  await _git(cwd, ['config', 'user.email', 'test@example.com']);
  File(p.join(cwd, 'README.md')).writeAsStringSync('hello');
  await _git(cwd, ['add', '--', 'README.md']);
  await _git(cwd, ['commit', '-q', '-m', 'initial']);
}

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
    // D6 gates writeFile behind trust/approval just like run() -- these two tests exercise the
    // plain read/write round-trip in a trusted workspace (no queueing); the untrusted-workspace
    // gating behavior itself is covered in the "write-gating consent tier (D6)" group below.
    test('writes and reads back a file inside the granted root', () async {
      roots.setTrusted(workspaceDir.path, true);
      await handler.writeFile({'path': 'notes.txt', 'content': 'hello'});
      final result = await handler.readFile({'path': 'notes.txt'});
      expect(result['content'], 'hello');
    });

    test('creates parent directories as needed', () async {
      roots.setTrusted(workspaceDir.path, true);
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

  group('write-gating consent tier (D6)', () {
    late ApprovalQueue approvals;

    setUp(() {
      approvals = ApprovalQueue();
      handler = TaskHandler(roots, approvals);
    });

    test('writeFile in an untrusted workspace queues for approval instead of writing immediately, '
        'then writes once approved', () async {
      final future = handler.writeFile({'path': 'notes.txt', 'content': 'hello'});
      expect(approvals.list(), hasLength(1));
      expect(approvals.list().single.kind, OperationKind.write);
      expect(File(p.join(workspaceDir.path, 'notes.txt')).existsSync(), isFalse);

      approvals.respond(approvals.list().single.id, true);
      await future;
      expect(File(p.join(workspaceDir.path, 'notes.txt')).readAsStringSync(), 'hello');
    });

    test('the queued write approval carries a real diff showing the proposed content', () async {
      unawaited(handler.writeFile({'path': 'notes.txt', 'content': 'hello world'}));
      final approval = approvals.list().single;
      expect(approval.diffPreview, contains('+hello world'));
      approvals.respond(approval.id, true);
    });

    test('rejecting a queued write throws and never writes it', () async {
      final future = handler.writeFile({'path': 'notes.txt', 'content': 'hello'});
      approvals.respond(approvals.list().single.id, false);
      await expectLater(future, throwsA(isA<RpcError>()));
      expect(File(p.join(workspaceDir.path, 'notes.txt')).existsSync(), isFalse);
    });

    test('an approved write is refused if the target file changed on disk while the approval was '
        'pending (staleness revalidation)', () async {
      final target = File(p.join(workspaceDir.path, 'notes.txt'))..writeAsStringSync('original');
      final future = handler.writeFile({'path': 'notes.txt', 'content': 'proposed'});
      final pending = approvals.list().single;
      // Someone/something else changes the file while the approval is still pending.
      target.writeAsStringSync('changed-out-from-under-it');
      approvals.respond(pending.id, true);
      await expectLater(future, throwsA(isA<RpcError>()));
      expect(target.readAsStringSync(), 'changed-out-from-under-it');
    });

    test('a trusted workspace never queues a write -- it applies immediately', () async {
      roots.setTrusted(workspaceDir.path, true);
      await handler.writeFile({'path': 'notes.txt', 'content': 'hello'});
      expect(approvals.list(), isEmpty);
      expect(File(p.join(workspaceDir.path, 'notes.txt')).readAsStringSync(), 'hello');
    });
  });

  group('writeFiles (D6 multi-file atomic edit)', () {
    late ApprovalQueue approvals;

    setUp(() {
      approvals = ApprovalQueue();
      handler = TaskHandler(roots, approvals);
      roots.setTrusted(workspaceDir.path, true); // trust by default; untrusted case tested below
    });

    test('writes multiple files in one call', () async {
      final result = await handler.writeFiles({
        'operations': [
          {'path': 'a.txt', 'content': 'A'},
          {'path': 'b.txt', 'content': 'B'},
        ],
      });
      expect(result['bytes_written'], 2);
      expect(File(p.join(workspaceDir.path, 'a.txt')).readAsStringSync(), 'A');
      expect(File(p.join(workspaceDir.path, 'b.txt')).readAsStringSync(), 'B');
    });

    test('supports a find/replace operation alongside a full-overwrite operation', () async {
      File(p.join(workspaceDir.path, 'existing.txt')).writeAsStringSync('hello world');
      await handler.writeFiles({
        'operations': [
          {'path': 'existing.txt', 'find': 'world', 'replace': 'there'},
          {'path': 'new.txt', 'content': 'brand new'},
        ],
      });
      expect(File(p.join(workspaceDir.path, 'existing.txt')).readAsStringSync(), 'hello there');
      expect(File(p.join(workspaceDir.path, 'new.txt')).readAsStringSync(), 'brand new');
    });

    test('a find/replace target that does not occur exactly once fails the WHOLE call before '
        'anything is written -- including other, valid operations', () async {
      File(p.join(workspaceDir.path, 'existing.txt')).writeAsStringSync('hello world');
      await expectLater(
        handler.writeFiles({
          'operations': [
            {'path': 'new.txt', 'content': 'this must not be written'},
            {'path': 'existing.txt', 'find': 'not present anywhere', 'replace': 'x'},
          ],
        }),
        throwsA(isA<RpcError>()),
      );
      expect(File(p.join(workspaceDir.path, 'new.txt')).existsSync(), isFalse);
      expect(File(p.join(workspaceDir.path, 'existing.txt')).readAsStringSync(), 'hello world');
    });

    test('a find/replace target that does not exist as a file fails validation before any write', () async {
      await expectLater(
        handler.writeFiles({
          'operations': [
            {'path': 'ok.txt', 'content': 'fine'},
            {'path': 'missing.txt', 'find': 'x', 'replace': 'y'},
          ],
        }),
        throwsA(isA<RpcError>()),
      );
      expect(File(p.join(workspaceDir.path, 'ok.txt')).existsSync(), isFalse);
    });

    test('refuses a batch spanning more than one granted root', () async {
      final otherDir = Directory(p.join(tempDir.path, 'other'))..createSync();
      roots.addRoot(otherDir.path, trusted: true);
      await expectLater(
        handler.writeFiles({
          'operations': [
            {'path': p.join(workspaceDir.path, 'a.txt'), 'content': 'A'},
            {'path': p.join(otherDir.path, 'b.txt'), 'content': 'B'},
          ],
        }),
        throwsA(isA<RpcError>()),
      );
    });

    test('an untrusted workspace queues the whole batch as one approval', () async {
      roots.setTrusted(workspaceDir.path, false);
      final future = handler.writeFiles({
        'operations': [
          {'path': 'a.txt', 'content': 'A'},
          {'path': 'b.txt', 'content': 'B'},
        ],
      });
      expect(approvals.list(), hasLength(1));
      final approval = approvals.list().single;
      expect(approval.diffPreview, allOf(contains('+A'), contains('+B')));
      approvals.respond(approval.id, true);
      await future;
      expect(File(p.join(workspaceDir.path, 'a.txt')).existsSync(), isTrue);
      expect(File(p.join(workspaceDir.path, 'b.txt')).existsSync(), isTrue);
    });
  });

  group('task isolation (D8 per-task git worktrees)', () {
    setUp(() async {
      await _initRepo(workspaceDir.path);
      roots.setTrusted(workspaceDir.path, true);
      handler = TaskHandler(roots);
    });

    test('isolate: false (or absent) keeps the pre-D8 behavior exactly', () async {
      final result = await handler.createTask(const {});
      expect(result['task_id'], isNotEmpty);
      expect(result.containsKey('root'), isFalse);
    });

    test('isolate: true provisions a worktree and every write/run for that task_id resolves only '
        'against it', () async {
      final created = await handler.createTask({
        'isolate': true,
        'task_key': 'session-1',
        'root': workspaceDir.path,
      });
      final taskId = created['task_id'] as String;
      final taskRoot = created['root'] as String;
      expect(taskRoot, isNot(workspaceDir.path));
      expect(Directory(taskRoot).existsSync(), isTrue);

      await handler.writeFile({'task_id': taskId, 'path': 'notes.txt', 'content': 'isolated'});
      expect(File(p.join(taskRoot, 'notes.txt')).existsSync(), isTrue);
      expect(File(p.join(workspaceDir.path, 'notes.txt')).existsSync(), isFalse);

      final read = await handler.readFile({'task_id': taskId, 'path': 'notes.txt'});
      expect(read['content'], 'isolated');
    }, testOn: '!windows');

    test('a second message in the same session (same task_key) reuses the existing worktree',
        () async {
      final first = await handler.createTask({
        'isolate': true,
        'task_key': 'session-1',
        'root': workspaceDir.path,
      });
      final second = await handler.createTask({
        'isolate': true,
        'task_key': 'session-1',
        'root': workspaceDir.path,
      });
      expect(second['task_id'], first['task_id']);
      expect(second['root'], first['root']);
    });

    test('two different sessions get two different, independent worktrees', () async {
      final first = await handler.createTask({
        'isolate': true,
        'task_key': 'session-1',
        'root': workspaceDir.path,
      });
      final second = await handler.createTask({
        'isolate': true,
        'task_key': 'session-2',
        'root': workspaceDir.path,
      });
      expect(second['task_id'], isNot(first['task_id']));
      expect(second['root'], isNot(first['root']));
    });

    test('an isolated task inherits the parent root\'s trust level exactly', () async {
      roots.setTrusted(workspaceDir.path, false);
      final created = await handler.createTask({
        'isolate': true,
        'task_key': 'session-1',
        'root': workspaceDir.path,
      });
      final taskId = created['task_id'] as String;
      expect(
        () => handler.run({
          'task_id': taskId,
          'command': ['sh', '-c', 'echo hi > x.txt'],
          'timeout_s': 5.0,
        }),
        throwsA(isA<RpcError>()),
      );
    }, testOn: '!windows');

    test('a write for an unknown task_id refuses rather than falling back to a cross-root search',
        () async {
      expect(
        () => handler.writeFile({'task_id': 'not_a_real_task', 'path': 'notes.txt', 'content': 'x'}),
        throwsA(isA<RpcError>()),
      );
      expect(File(p.join(workspaceDir.path, 'notes.txt')).existsSync(), isFalse);
    });

    test('run defaults its cwd to the task worktree and refuses a cwd outside it', () async {
      final created = await handler.createTask({
        'isolate': true,
        'task_key': 'session-1',
        'root': workspaceDir.path,
      });
      final taskId = created['task_id'] as String;
      final taskRoot = created['root'] as String;

      final result = await handler.run({
        'task_id': taskId,
        'command': ['sh', '-c', 'pwd'],
        'timeout_s': 5.0,
      });
      expect((result['stdout'] as String).trim(), taskRoot);

      expect(
        () => handler.run({
          'task_id': taskId,
          'cwd': workspaceDir.path,
          'command': ['sh', '-c', 'pwd'],
          'timeout_s': 5.0,
        }),
        throwsA(isA<ContainmentViolation>()),
      );
    }, testOn: '!windows');

    test('createTask with isolate: true and no explicit root when exactly one root is granted '
        'uses it', () async {
      final created = await handler.createTask({'isolate': true, 'task_key': 'session-1'});
      expect(created['root'], isNot(isNull));
    });

    test('createTask with isolate: true refuses without an explicit root when more than one is '
        'granted', () async {
      final otherDir = Directory(p.join(tempDir.path, 'other'))..createSync();
      await _initRepo(otherDir.path);
      roots.addRoot(otherDir.path, trusted: true);
      await expectLater(
        handler.createTask({'isolate': true, 'task_key': 'session-1'}),
        throwsA(isA<RpcError>()),
      );
    });

    group('reopenTask', () {
      test('re-registers a binding this handler instance never created, without a fresh git '
          'worktree add', () async {
        final other = TaskHandler(roots);
        final created = await other.createTask({
          'isolate': true,
          'task_key': 'session-1',
          'root': workspaceDir.path,
        });

        final reopened = await handler.reopenTask({
          'task_key': 'session-1',
          'task_id': created['task_id'],
          'root': workspaceDir.path,
          'worktree_path': created['root'],
          'branch': 'aura/task-${created['task_id']}',
        });
        expect(reopened['task_id'], created['task_id']);

        await handler.writeFile({
          'task_id': created['task_id'],
          'path': 'reopened.txt',
          'content': 'still works',
        });
        expect(
          File(p.join(created['root'] as String, 'reopened.txt')).readAsStringSync(),
          'still works',
        );
      });

      test('refuses when the named worktree no longer exists on disk', () async {
        await expectLater(
          handler.reopenTask({
            'task_key': 'session-1',
            'task_id': 't_ghost',
            'root': workspaceDir.path,
            'worktree_path': p.join(workspaceDir.path, '.aura-workspaces', 't_ghost'),
            'branch': 'aura/task-t_ghost',
          }),
          throwsA(isA<RpcError>()),
        );
      });
    });

    group('discardTaskWorkspace', () {
      test('removes the worktree/branch and forgets the binding', () async {
        final created = await handler.createTask({
          'isolate': true,
          'task_key': 'session-1',
          'root': workspaceDir.path,
        });
        final taskId = created['task_id'] as String;
        final taskRoot = created['root'] as String;

        final result = await handler.discardTaskWorkspace(taskId);
        expect(result['ok'], isTrue);
        expect(Directory(taskRoot).existsSync(), isFalse);
        expect(
          () => handler.readFile({'task_id': taskId, 'path': 'README.md'}),
          throwsA(isA<RpcError>()),
        );

        // A discarded task's key is free to be re-isolated fresh.
        final again = await handler.createTask({
          'isolate': true,
          'task_key': 'session-1',
          'root': workspaceDir.path,
        });
        expect(again['task_id'], isNot(taskId));
      });

      test('refuses to discard an unknown task_id', () async {
        await expectLater(handler.discardTaskWorkspace('nope'), throwsA(isA<RpcError>()));
      });
    });

    group('restart durability via TaskWorkspaceStore', () {
      late TaskWorkspaceStore store;

      setUp(() {
        store = TaskWorkspaceStore(File(p.join(tempDir.path, 'task_workspaces.json')));
      });

      test('a new TaskHandler instance self-heals a binding the store remembers', () async {
        final first = TaskHandler(roots, null, GitService(), store);
        final created = await first.createTask({
          'isolate': true,
          'task_key': 'session-1',
          'root': workspaceDir.path,
        });
        final taskId = created['task_id'] as String;
        final taskRoot = created['root'] as String;

        // Simulate a daemon restart: a fresh TaskHandler over the same persisted store.
        final second = TaskHandler(roots, null, GitService(), store);
        await second.writeFile({'task_id': taskId, 'path': 'after_restart.txt', 'content': 'ok'});
        expect(File(p.join(taskRoot, 'after_restart.txt')).existsSync(), isTrue);
      }, testOn: '!windows');

      test('drops a persisted binding whose worktree directory no longer exists on disk',
          () async {
        final first = TaskHandler(roots, null, GitService(), store);
        final created = await first.createTask({
          'isolate': true,
          'task_key': 'session-1',
          'root': workspaceDir.path,
        });
        Directory(created['root'] as String).deleteSync(recursive: true);

        final second = TaskHandler(roots, null, GitService(), store);
        expect(
          () => second.readFile({'task_id': created['task_id'], 'path': 'README.md'}),
          throwsA(isA<RpcError>()),
        );
        expect(store.load(), isEmpty);
      });
    });
  });
}
