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

  group('live view (G-05, SEC-08)', () {
    test('reflects grant, untrust and revoke saved after it was handed out', () {
      final workspace = Directory(p.join(tempDir.path, 'project3'))..createSync();
      final store = WorkspaceStore.forConfigDir(tempDir);
      final live = store.live;
      expect(live.all, isEmpty);

      // Saved through the same store (the control server's path): applied in place.
      store.save(store.load()..addRoot(workspace.path, trusted: true));
      expect(live.all.single.trusted, isTrue);
      expect(live.findContainingRoot(p.join(workspace.path, 'x.txt')), isNotNull);

      store.save(store.load()..setTrusted(workspace.path, false));
      expect(live.all.single.trusted, isFalse);

      // Saved by another store instance (the CLI in another process): seen after refreshLive.
      final cli = WorkspaceStore.forConfigDir(tempDir);
      cli.save(cli.load()..removeRoot(workspace.path));
      store.refreshLive();
      expect(live.all, isEmpty);
      expect(live.findContainingRoot(p.join(workspace.path, 'x.txt')), isNull);
    });
  });
}
