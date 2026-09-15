import 'dart:io';

import 'package:aura_hostd/src/containment.dart';
import 'package:aura_hostd/src/workspace_store.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  late Directory tempDir;

  setUp(() {
    tempDir = Directory.systemTemp.createTempSync('aura_hostd_store_test_');
  });

  tearDown(() {
    tempDir.deleteSync(recursive: true);
  });

  test('load() on a fresh config dir returns no roots', () {
    final store = WorkspaceStore.forConfigDir(tempDir);
    expect(store.load().all, isEmpty);
  });

  test('save() then load() round-trips granted roots and their trust flag', () {
    final workspace = Directory(p.join(tempDir.path, 'project'))..createSync();
    final store = WorkspaceStore.forConfigDir(tempDir);

    final roots = WorkspaceRoots(const []);
    roots.addRoot(workspace.path, trusted: true);
    store.save(roots);

    final reloaded = store.load();
    expect(reloaded.all, hasLength(1));
    expect(reloaded.all.single.canonicalPath, roots.all.single.canonicalPath);
    expect(reloaded.all.single.trusted, isTrue);
  });

  test('load() tolerates an empty file rather than throwing', () {
    final file = File(p.join(tempDir.path, 'workspaces.json'))..writeAsStringSync('');
    final store = WorkspaceStore(file);
    expect(store.load().all, isEmpty);
  });

  test('save() creates the config directory if missing', () {
    final nested = Directory(p.join(tempDir.path, 'nested', 'config'));
    final store = WorkspaceStore.forConfigDir(nested);
    final workspace = Directory(p.join(tempDir.path, 'project2'))..createSync();
    final roots = WorkspaceRoots(const []);
    roots.addRoot(workspace.path);
    store.save(roots);
    expect(File(p.join(nested.path, 'workspaces.json')).existsSync(), isTrue);
  });

  group('TaskWorkspaceStore (D8 per-task worktree bindings)', () {
    test('load() on a fresh config dir returns no entries', () {
      final store = TaskWorkspaceStore.forConfigDir(tempDir);
      expect(store.load(), isEmpty);
    });

    test('save() then load() round-trips every field', () {
      final store = TaskWorkspaceStore.forConfigDir(tempDir);
      final entry = TaskWorkspace(
        taskKey: 'session-1',
        taskId: 't_1',
        root: '/repo',
        worktreePath: '/repo/.aura-workspaces/t_1',
        branch: 'aura/task-t_1',
      );
      store.save([entry]);

      final reloaded = store.load();
      expect(reloaded, hasLength(1));
      expect(reloaded.single.taskKey, 'session-1');
      expect(reloaded.single.taskId, 't_1');
      expect(reloaded.single.root, '/repo');
      expect(reloaded.single.worktreePath, '/repo/.aura-workspaces/t_1');
      expect(reloaded.single.branch, 'aura/task-t_1');
    });

    test('load() tolerates an empty file rather than throwing', () {
      final file = File(p.join(tempDir.path, 'task_workspaces.json'))..writeAsStringSync('');
      final store = TaskWorkspaceStore(file);
      expect(store.load(), isEmpty);
    });

    test('save() creates the config directory if missing', () {
      final nested = Directory(p.join(tempDir.path, 'nested', 'config'));
      final store = TaskWorkspaceStore.forConfigDir(nested);
      store.save([
        TaskWorkspace(
          taskKey: 'session-1',
          taskId: 't_1',
          root: '/repo',
          worktreePath: '/repo/.aura-workspaces/t_1',
          branch: 'aura/task-t_1',
        ),
      ]);
      expect(File(p.join(nested.path, 'task_workspaces.json')).existsSync(), isTrue);
    });
  });
}
