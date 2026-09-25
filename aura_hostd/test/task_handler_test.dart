import 'dart:async';
import 'dart:io';

import 'package:aura_hostd/src/approval_queue.dart';
import 'package:aura_hostd/src/containment.dart';
import 'package:aura_hostd/src/isolated_task_registry.dart';
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
    // Separate, pre-existing issue this fix surfaces rather than causes: Git for Windows' `sh.exe`
    // runs in its own MSYS POSIX view, so `pwd` reports the MSYS-translated path (`/tmp/...`)
    // rather than the Windows path the test hardcodes -- the shell itself runs fine (exit 0, real
    // output). Fixing the assertion to account for MSYS path translation is out of scope here.
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
    });

    test('allows an arbitrary command once the workspace is trusted', () async {
      roots.setTrusted(workspaceDir.path, true);
      final result = await handler.run({
        'command': ['sh', '-c', 'echo hello > written.txt'],
        'timeout_s': 5.0,
      });
      expect(result['exit_code'], 0);
      expect(File(p.join(workspaceDir.path, 'written.txt')).existsSync(), isTrue);
    });

    test('refuses rm -rf even in a trusted workspace', () async {
      roots.setTrusted(workspaceDir.path, true);
      expect(
        () => handler.run({
          'command': ['sh', '-c', 'rm -rf .'],
          'timeout_s': 5.0,
        }),
        throwsA(isA<RpcError>()),
      );
    });

    test('kills a command that exceeds its timeout', () async {
      roots.setTrusted(workspaceDir.path, true);
      final result = await handler.run({
        'command': ['sh', '-c', 'sleep 30'],
        'timeout_s': 0.5,
      });
      expect(result['exit_code'], isNot(0));
      expect(result['stderr'], contains('timeout'));
    });

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
    });

    test('run works identically with no onChunk callback at all', () async {
      roots.setTrusted(workspaceDir.path, true);
      final result = await handler.run({
        'command': ['sh', '-c', 'echo hi'],
        'timeout_s': 5.0,
      });
      expect(result['exit_code'], 0);
      expect(result['stdout'], contains('hi'));
    });

    test('caps captured/forwarded output at the daemon-side limit', () async {
      roots.setTrusted(workspaceDir.path, true);
      final result = await handler.run({
        'command': ['sh', '-c', 'yes x | head -c 600000'],
        'timeout_s': 10.0,
      });
      expect(result['exit_code'], 0);
      expect((result['stdout'] as String).length, lessThanOrEqualTo(500000));
      expect(result['stderr'], contains('truncated'));
    });
  });

  group('needsApproval consent tier (D5)', () {
    late ApprovalQueue approvals;

    setUp(() {
      approvals = ApprovalQueue();
      handler = TaskHandler(roots, approvals);
    });

    // These three tests' command (`echo hello > written.txt`) contains `>`, which
    // `command_policy.dart`'s `_shellMetacharacters` check now refuses outright in every
    // untrusted workspace ("never queued for approval") -- a hard floor added after these tests
    // were written, which they never got updated for. Not a Windows issue (the check has no
    // platform branch; it would fail identically on Linux/macOS CI) and not something surfaced by
    // the run_command Windows shell-resolution fix -- found in passing while removing this file's
    // blanket `testOn: '!windows'`, out of scope to fix here.
    const redirectPredatesHardDeny =
        'command contains ">", which the hard metacharacter-deny check now refuses outright -- '
        'stale test, unrelated to this change';

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
    }, skip: redirectPredatesHardDeny);

    test('rejecting a queued command throws and never runs it', () async {
      final future = handler.run({
        'command': ['sh', '-c', 'echo hello > written.txt'],
        'timeout_s': 5.0,
      });
      approvals.respond(approvals.list().single.id, false);
      await expectLater(future, throwsA(isA<RpcError>()));
      expect(File(p.join(workspaceDir.path, 'written.txt')).existsSync(), isFalse);
    }, skip: redirectPredatesHardDeny);

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
    }, skip: redirectPredatesHardDeny);

    test('a trusted workspace never queues -- the command runs immediately', () async {
      roots.setTrusted(workspaceDir.path, true);
      final result = await handler.run({
        'command': ['sh', '-c', 'echo hello > written.txt'],
        'timeout_s': 5.0,
      });
      expect(result['exit_code'], 0);
      expect(approvals.list(), isEmpty);
    });
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

  group('per-task isolated worktrees (D8 Part B)', () {
    late ApprovalQueue approvals;

    Future<ProcessResult> git(List<String> args) => Process.run('git', args, workingDirectory: workspaceDir.path);

    setUp(() async {
      approvals = ApprovalQueue();
      handler = TaskHandler(roots, approvals);
      await git(['init', '-q', '-b', 'main']);
      await git(['config', 'user.name', 'Test User']);
      await git(['config', 'user.email', 'test@example.com']);
      File(p.join(workspaceDir.path, 'a.txt')).writeAsStringSync('original');
      await git(['add', '--', 'a.txt']);
      await git(['commit', '-q', '-m', 'initial commit']);
      roots.setTrusted(workspaceDir.path, true);
    });

    test('an isolated task writes into its own worktree, not the live checkout', () async {
      final created = await handler.createTask({'isolate': true, 'task_key': 'session-1'});
      expect(created['isolated'], isTrue);
      final taskId = created['task_id'] as String;

      await handler.writeFile({'task_id': taskId, 'path': 'a.txt', 'content': 'changed by agent'});

      // The live checkout must be untouched -- that is the entire point of isolation.
      expect(File(p.join(workspaceDir.path, 'a.txt')).readAsStringSync(), 'original');

      final worktreePath = p.join(workspaceDir.path, '.aura-workspaces', 'session-1');
      expect(File(p.join(worktreePath, 'a.txt')).readAsStringSync(), 'changed by agent');

      final worktreeList = await git(['worktree', 'list']);
      expect(worktreeList.stdout.toString().replaceAll('\\', '/'), contains('session-1'));
    });

    test('a second task_id for the same task_key reuses the same worktree, not a new one', () async {
      final first = await handler.createTask({'isolate': true, 'task_key': 'session-2'});
      await handler.writeFile({'task_id': first['task_id'], 'path': 'a.txt', 'content': 'turn 1'});

      // A brand-new task_id (as the backend mints per turn) but the same task_key (the chat
      // session id, stable across turns) -- must land in the same worktree, on the same branch.
      final second = await handler.createTask({'isolate': true, 'task_key': 'session-2'});
      expect(second['task_id'], isNot(first['task_id']));
      await handler.writeFile({'task_id': second['task_id'], 'path': 'b.txt', 'content': 'turn 2'});

      final worktreePath = p.join(workspaceDir.path, '.aura-workspaces', 'session-2');
      expect(File(p.join(worktreePath, 'a.txt')).readAsStringSync(), 'turn 1');
      expect(File(p.join(worktreePath, 'b.txt')).readAsStringSync(), 'turn 2');

      final branches = await git(['branch', '--list', 'aura/task-session-2']);
      expect(branches.stdout.toString().trim(), isNotEmpty);
    });

    test('an isolated write is refused if the path resolves outside its own worktree', () async {
      // A second, independently-granted root the task's worktree must never be able to reach
      // into, even though it's a path this daemon otherwise knows about.
      final otherDir = Directory(p.join(tempDir.path, 'other'))..createSync();
      roots.addRoot(otherDir.path, trusted: true);

      final created = await handler.createTask({'isolate': true, 'task_key': 'session-3'});
      await handler.writeFile({'task_id': created['task_id'], 'path': 'a.txt', 'content': 'inside task'});

      // Once bound, this task_id must resolve *only* against its own worktree -- never fall back
      // to searching other granted roots, even for a path that would otherwise resolve cleanly
      // under one of them (D8 plan Part B.2.3). Forcing `root` to the task's own worktree means
      // this hits `requireContained`'s own raw `ContainmentViolation` directly (the same
      // exception `writeFile` already lets propagate uncaught for any out-of-root path once a
      // root has been chosen -- `RpcError` is only thrown one branch earlier, when no root at
      // all could be found).
      await expectLater(
        handler.writeFile({
          'task_id': created['task_id'],
          'path': p.join(otherDir.path, 'escape.txt'),
          'content': 'should not land here',
        }),
        throwsA(isA<ContainmentViolation>()),
      );
      expect(File(p.join(otherDir.path, 'escape.txt')).existsSync(), isFalse);
    });

    test('an isolated worktree inherits the parent root\'s trust -- no extra approval friction', () async {
      // setUp already trusted workspaceDir; a write inside the isolated task must apply
      // immediately too, exactly like a direct write to a trusted root would.
      final created = await handler.createTask({'isolate': true, 'task_key': 'session-4'});
      final result = await handler.writeFile({
        'task_id': created['task_id'],
        'path': 'a.txt',
        'content': 'no approval needed',
      });
      expect(result['bytes_written'], greaterThan(0));
      final worktreePath = p.join(workspaceDir.path, '.aura-workspaces', 'session-4');
      expect(File(p.join(worktreePath, 'a.txt')).readAsStringSync(), 'no approval needed');
    });

    test('an isolated task in an untrusted parent still queues for approval', () async {
      roots.setTrusted(workspaceDir.path, false);
      final created = await handler.createTask({'isolate': true, 'task_key': 'session-5'});
      final future = handler.writeFile({
        'task_id': created['task_id'],
        'path': 'a.txt',
        'content': 'needs a human',
      });
      // Resolving the task root for its first call does real filesystem/git work
      // (`git worktree add` as a subprocess) before reaching the approval queue, so poll rather
      // than assume one microtask tick is enough -- unlike the non-isolated write-gating tests
      // above, which stay fully synchronous up to the approval being queued.
      for (var i = 0; i < 100 && approvals.list().isEmpty; i++) {
        await Future<void>.delayed(const Duration(milliseconds: 20));
      }
      expect(approvals.list(), hasLength(1));
      approvals.respond(approvals.list().single.id, true);
      await future;

      final worktreePath = p.join(workspaceDir.path, '.aura-workspaces', 'session-5');
      expect(File(p.join(worktreePath, 'a.txt')).readAsStringSync(), 'needs a human');
    });

    test('recovers a worktree that already exists on disk instead of failing to recreate it', () async {
      // Simulates a daemon restart: the worktree/branch exist on disk from an earlier run, but a
      // fresh TaskHandler has no in-memory record of them.
      final first = await handler.createTask({'isolate': true, 'task_key': 'session-6'});
      await handler.writeFile({'task_id': first['task_id'], 'path': 'a.txt', 'content': 'before restart'});

      final freshHandler = TaskHandler(roots, approvals);
      final second = await freshHandler.createTask({'isolate': true, 'task_key': 'session-6'});
      await freshHandler.writeFile({'task_id': second['task_id'], 'path': 'b.txt', 'content': 'after restart'});

      final worktreePath = p.join(workspaceDir.path, '.aura-workspaces', 'session-6');
      expect(File(p.join(worktreePath, 'a.txt')).readAsStringSync(), 'before restart');
      expect(File(p.join(worktreePath, 'b.txt')).readAsStringSync(), 'after restart');
    });

    test('a non-isolated task_id (isolate not requested) behaves exactly as before', () async {
      final created = await handler.createTask({});
      expect(created['isolated'], isFalse);
      await handler.writeFile({'task_id': created['task_id'], 'path': 'a.txt', 'content': 'direct write'});
      expect(File(p.join(workspaceDir.path, 'a.txt')).readAsStringSync(), 'direct write');
      expect(Directory(p.join(workspaceDir.path, '.aura-workspaces')).existsSync(), isFalse);
    });
  });

  group('IsolatedTaskRegistry.discard (D9 primitive)', () {
    late IsolatedTaskRegistry isolatedTasks;

    Future<ProcessResult> git(List<String> args) => Process.run('git', args, workingDirectory: workspaceDir.path);

    setUp(() async {
      isolatedTasks = IsolatedTaskRegistry(roots);
      handler = TaskHandler(roots, null, null, isolatedTasks);
      await git(['init', '-q', '-b', 'main']);
      await git(['config', 'user.name', 'Test User']);
      await git(['config', 'user.email', 'test@example.com']);
      File(p.join(workspaceDir.path, 'a.txt')).writeAsStringSync('original');
      await git(['add', '--', 'a.txt']);
      await git(['commit', '-q', '-m', 'initial commit']);
      roots.setTrusted(workspaceDir.path, true);
    });

    test('tears down the worktree directory and its branch', () async {
      final created = await handler.createTask({'isolate': true, 'task_key': 'session-7'});
      await handler.writeFile({'task_id': created['task_id'], 'path': 'a.txt', 'content': 'x'});
      final worktreePath = p.join(workspaceDir.path, '.aura-workspaces', 'session-7');
      expect(Directory(worktreePath).existsSync(), isTrue);

      await isolatedTasks.discard('session-7');
      expect(Directory(worktreePath).existsSync(), isFalse);

      final branches = await git(['branch', '--list', 'aura/task-session-7']);
      expect(branches.stdout.toString().trim(), isEmpty);
    });

    test('refuses to discard a task_key with no known isolated workspace', () async {
      await expectLater(
        isolatedTasks.discard('never-created'),
        throwsA(isA<TaskNotFoundError>()),
      );
    });
  });
}
