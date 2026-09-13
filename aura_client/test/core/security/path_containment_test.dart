/// Tests for client-side path containment utilities.
///
/// These test vectors MUST match the hostd containment tests in
/// `aura_hostd/test/containment_test.dart` to prevent drift.
library;

import 'dart:io';
import 'package:path/path.dart' as p;
import 'package:flutter_test/flutter_test.dart';
import 'package:aura_client/core/security/path_containment.dart';

void main() {
  group('Client path containment', () {
    final testDir = Directory.systemTemp.createTempSync('aura_containment_test_');
    final workspace = Directory(p.join(testDir.path, 'workspace'))..createSync(recursive: true);
    final secretDir = Directory(p.join(testDir.path, 'secret'))..createSync(recursive: true);
    final srcDir = Directory(p.join(workspace.path, 'src'))..createSync(recursive: true);
    final deepDir = Directory(p.join(srcDir.path, 'deep', 'nested'))..createSync(recursive: true);

    // Create test files
    File(p.join(srcDir.path, 'a.dart')).writeAsStringSync('void main() {}');
    File(p.join(deepDir.path, 'file.dart')).writeAsStringSync('void deep() {}');
    File(p.join(secretDir.path, 'secret.txt')).writeAsStringSync('secret');

    tearDownAll(() {
      try {
        testDir.deleteSync(recursive: true);
      } catch (_) {}
    });

    group('requireContainedClient', () {
      test('allows file inside workspace', () {
        final result = requireContainedClient(workspace.path, 'src/a.dart');
        expect(result, equals(p.join(workspace.path, 'src', 'a.dart')));
      });

      test('allows file in deep subdirectory', () {
        final result = requireContainedClient(workspace.path, 'src/deep/nested/file.dart');
        expect(result, equals(p.join(workspace.path, 'src', 'deep', 'nested', 'file.dart')));
      });

      test('allows workspace root itself', () {
        final result = requireContainedClient(workspace.path, '');
        expect(result, equals(workspace.path));
      });

      test('allows dot path', () {
        final result = requireContainedClient(workspace.path, '.');
        expect(result, equals(workspace.path));
      });

      test('denies parent traversal', () {
        expect(
          () => requireContainedClient(workspace.path, '../secret/secret.txt'),
          throwsA(isA<ClientContainmentViolation>()),
        );
      });

      test('denies double parent traversal', () {
        expect(
          () => requireContainedClient(p.join(workspace.path, 'src'), '../../secret/secret.txt'),
          throwsA(isA<ClientContainmentViolation>()),
        );
      });

      test('denies absolute path outside root', () {
        expect(
          () => requireContainedClient(workspace.path, secretDir.path),
          throwsA(isA<ClientContainmentViolation>()),
        );
      });

      test('denies dot-dot at root', () {
        expect(
          () => requireContainedClient(workspace.path, '..'),
          throwsA(isA<ClientContainmentViolation>()),
        );
      });
    });

    group('validateWorkspaceRoot', () {
      test('accepts valid workspace directory', () {
        validateWorkspaceRoot(workspace.path);
      });

      test('rejects non-existent path', () {
        expect(
          () => validateWorkspaceRoot(p.join(testDir.path, 'nonexistent')),
          throwsA(isA<ClientInvalidRootError>()),
        );
      });

      test('rejects file (not directory)', () {
        final file = File(p.join(workspace.path, 'src', 'a.dart'));
        expect(
          () => validateWorkspaceRoot(file.path),
          throwsA(isA<ClientInvalidRootError>()),
        );
      });
    });

    group('findContainingRootClient', () {
      test('finds root containing path', () {
        final roots = [workspace.path, secretDir.path];
        final result = findContainingRootClient(roots, p.join(workspace.path, 'src', 'a.dart'));
        expect(result, equals(workspace.path));
      });

      test('returns null for path outside all roots', () {
        final roots = [workspace.path];
        final result = findContainingRootClient(roots, secretDir.path);
        expect(result, isNull);
      });
    });
  });

  group('Shared test vectors', () {
    // Each vector's `root`/`path` are symbolic names (see path_containment.dart's
    // containmentTestVectors doc). We map the known symbolic names to real,
    // freshly-created directories per test so the vectors exercise the actual
    // resolution/denylist logic instead of merely checking "doesn't crash".
    late Directory tempDir;
    late Directory workspaceDir;
    late Directory workspaceADir;
    late Directory secretDir;
    late File secretFile;

    setUp(() {
      tempDir = Directory.systemTemp.createTempSync('aura_vector_test_');
      workspaceDir = Directory(p.join(tempDir.path, 'workspace'))..createSync(recursive: true);
      workspaceADir = Directory(p.join(workspaceDir.path, 'a'))..createSync(recursive: true);
      secretDir = Directory(p.join(tempDir.path, 'secret'))..createSync(recursive: true);
      secretFile = File(p.join(secretDir.path, 'secret.txt'))..writeAsStringSync('secret');
    });

    tearDown(() {
      try {
        tempDir.deleteSync(recursive: true);
      } catch (_) {}
    });

    /// Resolves a vector's symbolic root name to a real path on disk, or
    /// returns the name unchanged if it's already a literal absolute path
    /// (e.g. `/etc`, `C:\`) — those exist only on their native platform.
    String resolveRoot(String symbolicRoot) {
      switch (symbolicRoot) {
        case 'workspace':
          return workspaceDir.path;
        case 'workspace/a':
          return workspaceADir.path;
        case 'home':
          return (Platform.isWindows
              ? Platform.environment['USERPROFILE']
              : Platform.environment['HOME'])!;
        default:
          return symbolicRoot;
      }
    }

    /// Derives the relative path to feed into [requireContainedClient] from a
    /// vector's symbolic `root`/`path` pair, or `null` if the vector's path is
    /// a non-literal placeholder that can't be derived this way (see the
    /// vector's own `note`).
    String? resolveRelativePath(String symbolicRoot, String symbolicPath) {
      if (symbolicPath == symbolicRoot) return '';
      if (symbolicPath == '' || symbolicPath == '.' || symbolicPath == '..') {
        return symbolicPath;
      }
      final prefix = '$symbolicRoot/';
      if (symbolicPath.startsWith(prefix)) {
        return symbolicPath.substring(prefix.length);
      }
      return null;
    }

    for (final vector in containmentTestVectors) {
      if (vector.platform != null && vector.platform != Platform.operatingSystem) {
        continue; // Skip platform-specific vectors
      }

      test(vector.name, () {
        if (vector.requiresSymlink) {
          final linkPath = p.join(workspaceDir.path, 'link');
          Link link;
          try {
            link = Link(linkPath)..createSync(secretDir.path);
          } catch (e) {
            markTestSkipped('symlink creation unsupported in this environment: $e');
            return;
          }
          addTearDown(() {
            try {
              link.deleteSync();
            } catch (_) {}
          });
          expect(
            () => requireContainedClient(workspaceDir.path, 'link'),
            throwsA(isA<ClientContainmentViolation>()),
            reason: vector.note,
          );
          return;
        }

        if (vector.expected == ContainmentResult.denyRoot) {
          final resolvedRoot = resolveRoot(vector.root);
          if (!Directory(resolvedRoot).existsSync()) {
            markTestSkipped('$resolvedRoot does not exist on this platform');
            return;
          }
          expect(
            () => validateWorkspaceRoot(resolvedRoot),
            throwsA(isA<ClientInvalidRootError>()),
            reason: vector.note,
          );
          return;
        }

        final resolvedRoot = resolveRoot(vector.root);
        if (!Directory(resolvedRoot).existsSync()) {
          markTestSkipped('$resolvedRoot does not exist on this platform');
          return;
        }

        final relative = resolveRelativePath(vector.root, vector.path);
        if (relative == null) {
          final looksAbsolute =
              vector.path.startsWith('/') || RegExp(r'^[A-Za-z]:\\').hasMatch(vector.path);
          if (looksAbsolute && vector.expected == ContainmentResult.deny) {
            // Exercise the same "absolute path outside root" guarantee with a
            // real path instead of the vector's placeholder.
            expect(
              () => requireContainedClient(resolvedRoot, secretFile.path),
              throwsA(isA<ClientContainmentViolation>()),
              reason: vector.note,
            );
            return;
          }
          markTestSkipped(
            'vector path is a non-literal placeholder, not directly constructible: ${vector.note}',
          );
          return;
        }

        if (vector.expected == ContainmentResult.allow) {
          expect(
            () => requireContainedClient(resolvedRoot, relative),
            returnsNormally,
            reason: vector.note,
          );
        } else {
          expect(
            () => requireContainedClient(resolvedRoot, relative),
            throwsA(isA<ClientContainmentViolation>()),
            reason: vector.note,
          );
        }
      });
    }
  });
}
