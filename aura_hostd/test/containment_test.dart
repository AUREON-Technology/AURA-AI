import 'dart:io';

import 'package:aura_hostd/src/containment.dart';
import 'package:path/path.dart' as p;
import 'package:test/test.dart';

void main() {
  late Directory tempDir;
  late Directory workspaceDir;

  setUp(() {
    tempDir = Directory.systemTemp.createTempSync('aura_hostd_containment_test_');
    workspaceDir = Directory(p.join(tempDir.path, 'workspace'))..createSync();
  });

  tearDown(() {
    tempDir.deleteSync(recursive: true);
  });

  group('WorkspaceRoots.addRoot', () {
    test('accepts an existing directory', () {
      final roots = WorkspaceRoots(const []);
      final root = roots.addRoot(workspaceDir.path);
      expect(root.canonicalPath, File(workspaceDir.path).resolveSymbolicLinksSync());
      expect(roots.all, hasLength(1));
    });

    test('rejects a path that does not exist', () {
      final roots = WorkspaceRoots(const []);
      expect(
        () => roots.addRoot(p.join(tempDir.path, 'does_not_exist')),
        throwsA(isA<InvalidRootError>()),
      );
    });

    test('rejects the filesystem root', () {
      final roots = WorkspaceRoots(const []);
      expect(() => roots.addRoot('/'), throwsA(isA<InvalidRootError>()));
    });

    test('rejects a system directory even when it exists', () {
      if (!Directory('/etc').existsSync()) {
        return; // platform without /etc (e.g. a bare Windows runner) -- nothing to assert here.
      }
      final roots = WorkspaceRoots(const []);
      expect(() => roots.addRoot('/etc'), throwsA(isA<InvalidRootError>()));
    });

    test('re-granting the same path updates rather than duplicates', () {
      final roots = WorkspaceRoots(const []);
      roots.addRoot(workspaceDir.path);
      roots.addRoot(workspaceDir.path, trusted: true);
      expect(roots.all, hasLength(1));
      expect(roots.all.single.trusted, isTrue);
    });
  });

  group('WorkspaceRoots.requireContained', () {
    test('allows a file directly inside the root', () {
      final roots = WorkspaceRoots(const []);
      final root = roots.addRoot(workspaceDir.path);
      final resolved = roots.requireContained(root, 'src/main.py');
      expect(resolved, p.join(root.canonicalPath, 'src', 'main.py'));
    });

    test('allows an absolute path that resolves inside the root', () {
      final roots = WorkspaceRoots(const []);
      final root = roots.addRoot(workspaceDir.path);
      final absolute = p.join(root.canonicalPath, 'nested', 'file.txt');
      final resolved = roots.requireContained(root, absolute);
      expect(resolved, absolute);
    });

    test('rejects a relative escape with ".."', () {
      final roots = WorkspaceRoots(const []);
      final root = roots.addRoot(workspaceDir.path);
      expect(
        () => roots.requireContained(root, '../outside.txt'),
        throwsA(isA<ContainmentViolation>()),
      );
    });

    test('rejects an absolute path outside the root', () {
      final roots = WorkspaceRoots(const []);
      final root = roots.addRoot(workspaceDir.path);
      final outside = Directory(p.join(tempDir.path, 'sibling'))..createSync();
      expect(
        () => roots.requireContained(root, p.join(outside.path, 'file.txt')),
        throwsA(isA<ContainmentViolation>()),
      );
    });

    test('rejects a symlink inside the root that points outside it', () {
      if (Platform.isWindows) {
        return; // symlink creation needs elevated privileges on Windows CI runners.
      }
      final outside = Directory(p.join(tempDir.path, 'secret'))..createSync();
      File(p.join(outside.path, 'passwords.txt')).writeAsStringSync('hunter2');

      final roots = WorkspaceRoots(const []);
      final root = roots.addRoot(workspaceDir.path);
      final linkPath = p.join(root.canonicalPath, 'escape_link');
      Link(linkPath).createSync(outside.path);

      expect(
        () => roots.requireContained(root, p.join('escape_link', 'passwords.txt')),
        throwsA(isA<ContainmentViolation>()),
      );
    });

    test('rejects a directory junction inside the root that points outside it', () {
      if (!Platform.isWindows) {
        return; // junctions are an NTFS-specific reparse point; nothing to test elsewhere.
      }
      final outside = Directory(p.join(tempDir.path, 'secret_junction_target'))..createSync();
      File(p.join(outside.path, 'passwords.txt')).writeAsStringSync('hunter2');

      final roots = WorkspaceRoots(const []);
      final root = roots.addRoot(workspaceDir.path);
      final junctionPath = p.join(root.canonicalPath, 'escape_junction');

      // Unlike the Windows *symlink* test above (which needs elevation or Developer Mode and so
      // is skipped on ordinary CI runners), a directory junction needs no special privilege to
      // create -- exactly why it's the escape class worth testing separately. `Link` in dart:io
      // always creates a real NTFS *symbolic* link on Windows (`CreateSymbolicLink`); a junction
      // is a different reparse tag (`IO_REPARSE_TAG_MOUNT_POINT` vs `IO_REPARSE_TAG_SYMLINK`) that
      // dart:io has no API for, so this shells out to the same tool that would actually create
      // one, whether from a real attacker or a legitimate pre-existing junction in a project.
      final result = Process.runSync('cmd', ['/c', 'mklink', '/J', junctionPath, outside.path]);
      expect(result.exitCode, 0, reason: 'mklink /J failed: ${result.stderr}');

      // `resolveSymbolicLinksSync` on Windows is backed by CreateFileW *without*
      // FILE_FLAG_OPEN_REPARSE_POINT + GetFinalPathNameByHandle (dart-lang/sdk's
      // runtime/bin/file_win.cc, File::GetCanonicalPath) -- opening a handle that way makes
      // Windows itself transparently follow any reparse point along the path, junction or
      // symlink alike, before the handle is even returned. So this is expected to already pass
      // without any change to containment.dart; the point of this test is proving that (Phase 0
      // desktop audit's "Windows junctions are an untested escape class" finding) rather than
      // fixing a bug in it.
      expect(
        () => roots.requireContained(root, p.join('escape_junction', 'passwords.txt')),
        throwsA(isA<ContainmentViolation>()),
      );
    });

    test('does not require the target to already exist', () {
      final roots = WorkspaceRoots(const []);
      final root = roots.addRoot(workspaceDir.path);
      // A write to a brand-new file must be resolvable for containment purposes even though
      // nothing exists at that path yet.
      final resolved = roots.requireContained(root, 'new/nested/file.txt');
      expect(resolved, p.join(root.canonicalPath, 'new', 'nested', 'file.txt'));
    });
  });

  group('WorkspaceRoots.findContainingRoot', () {
    test('finds the root that contains an absolute path among several granted roots', () {
      final other = Directory(p.join(tempDir.path, 'other'))..createSync();
      final roots = WorkspaceRoots(const []);
      roots.addRoot(other.path);
      final root = roots.addRoot(workspaceDir.path);
      final target = p.join(root.canonicalPath, 'file.txt');
      final found = roots.findContainingRoot(target);
      expect(found?.canonicalPath, root.canonicalPath);
    });

    test('returns null when no granted root contains the path', () {
      final roots = WorkspaceRoots(const []);
      roots.addRoot(workspaceDir.path);
      final outside = Directory(p.join(tempDir.path, 'elsewhere'))..createSync();
      expect(roots.findContainingRoot(p.join(outside.path, 'file.txt')), isNull);
    });
  });

  group('setTrusted / removeRoot', () {
    test('setTrusted flips the flag for a granted root', () {
      final roots = WorkspaceRoots(const []);
      final root = roots.addRoot(workspaceDir.path);
      expect(root.trusted, isFalse);
      final updated = roots.setTrusted(workspaceDir.path, true);
      expect(updated?.trusted, isTrue);
    });

    test('setTrusted returns null for an ungranted path', () {
      final roots = WorkspaceRoots(const []);
      expect(roots.setTrusted(workspaceDir.path, true), isNull);
    });

    test('removeRoot removes a granted root', () {
      final roots = WorkspaceRoots(const []);
      roots.addRoot(workspaceDir.path);
      expect(roots.removeRoot(workspaceDir.path), isTrue);
      expect(roots.all, isEmpty);
    });
  });
}
