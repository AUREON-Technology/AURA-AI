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

    // requireContainedClient returns the *canonical* path, which is not necessarily the
    // spelling the temp dir came back with: on Windows the runner's temp dir can be an 8.3 short
    // name (`RUNNER~1`) while the canonical form is long, and on macOS `/var` is a symlink to
    // `/private/var`. Compare against the canonical workspace path, not `workspace.path`.
    final canonicalWorkspace = workspace.resolveSymbolicLinksSync();

    tearDownAll(() {
      try {
        testDir.deleteSync(recursive: true);
      } catch (_) {}
    });

    group('requireContainedClient', () {
      test('allows file inside workspace', () {
        final result = requireContainedClient(workspace.path, 'src/a.dart');
        expect(result, equals(p.join(canonicalWorkspace, 'src', 'a.dart')));
      });

      test('allows file in deep subdirectory', () {
        final result = requireContainedClient(workspace.path, 'src/deep/nested/file.dart');
        expect(result, equals(p.join(canonicalWorkspace, 'src', 'deep', 'nested', 'file.dart')));
      });

      test('allows workspace root itself', () {
        final result = requireContainedClient(workspace.path, '');
        expect(result, equals(canonicalWorkspace));
      });

      test('allows dot path', () {
        final result = requireContainedClient(workspace.path, '.');
        expect(result, equals(canonicalWorkspace));
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

  // Regression for the Windows-only CI failure (run 40719101490): the runner's temp dir came
  // back as an 8.3 short path on one side and a long path on the other, and the root was
  // compared without being canonicalized, so contained paths were refused. 8.3 names cannot be
  // created on Linux, so these tests use the closest analogue that runs everywhere: a root
  // reached through a symlink, i.e. a second, non-canonical spelling of the same directory.
  group('Aliased root (Linux analogue of a Windows 8.3 short name)', () {
    late Directory tempDir;
    late String realRoot; // canonical spelling
    late String aliasRoot; // non-canonical spelling of the same directory
    late String outsideFile;
    late String siblingFile;
    String? aliasError;

    setUp(() {
      tempDir = Directory.systemTemp.createTempSync('aura_alias_test_');
      final base = tempDir.resolveSymbolicLinksSync();
      realRoot = p.join(base, 'work');
      Directory(p.join(realRoot, 'src')).createSync(recursive: true);
      File(p.join(realRoot, 'src', 'a.dart')).writeAsStringSync('void main() {}');
      Directory(p.join(base, 'outside')).createSync();
      outsideFile = p.join(base, 'outside', 'secret.txt');
      File(outsideFile).writeAsStringSync('secret');
      // `/work` vs `/work2`: a sibling whose name merely starts with the root's name.
      Directory(p.join(base, 'work2')).createSync();
      siblingFile = p.join(base, 'work2', 'b.txt');
      File(siblingFile).writeAsStringSync('sibling');
      aliasRoot = p.join(base, 'alias');
      aliasError = null;
      try {
        Link(aliasRoot).createSync(realRoot);
      } catch (e) {
        aliasError = '$e';
      }
    });

    tearDown(() {
      try {
        tempDir.deleteSync(recursive: true);
      } catch (_) {}
    });

    /// Skips the current test when this host cannot create the alias (e.g. Windows without
    /// symlink privilege). Returns true if the test should return early.
    bool noAlias() {
      if (aliasError == null) return false;
      markTestSkipped('symlink creation unsupported in this environment: $aliasError');
      return true;
    }

    test('allows a file inside the workspace when the root is given by its alias', () {
      if (noAlias()) return;
      expect(requireContainedClient(aliasRoot, 'src/a.dart'), p.join(realRoot, 'src', 'a.dart'));
    });

    test('allows the root itself and the dot path when the root is given by its alias', () {
      if (noAlias()) return;
      expect(requireContainedClient(aliasRoot, ''), realRoot);
      expect(requireContainedClient(aliasRoot, '.'), realRoot);
    });

    test('allows an absolute path spelled through the alias against the canonical root', () {
      if (noAlias()) return;
      final viaAlias = p.join(aliasRoot, 'src', 'a.dart');
      expect(requireContainedClient(realRoot, viaAlias), p.join(realRoot, 'src', 'a.dart'));
    });

    test('allows an absolute canonical path against the alias root', () {
      if (noAlias()) return;
      final canonical = p.join(realRoot, 'src', 'a.dart');
      expect(requireContainedClient(aliasRoot, canonical), canonical);
    });

    test('judges a path that does not exist yet by its parent', () {
      if (noAlias()) return;
      expect(requireContainedClient(aliasRoot, 'src/new.dart'), p.join(realRoot, 'src', 'new.dart'));
      expect(
        requireContainedClient(aliasRoot, 'not/yet/created.dart'),
        p.join(realRoot, 'not', 'yet', 'created.dart'),
      );
      expect(
        () => requireContainedClient(aliasRoot, '../outside/new.txt'),
        throwsA(isA<ClientContainmentViolation>()),
      );
    });

    test('still refuses .. traversal out of an aliased root', () {
      if (noAlias()) return;
      expect(
        () => requireContainedClient(aliasRoot, '../outside/secret.txt'),
        throwsA(isA<ClientContainmentViolation>()),
      );
      expect(
        () => requireContainedClient(aliasRoot, '..'),
        throwsA(isA<ClientContainmentViolation>()),
      );
      expect(
        () => requireContainedClient(aliasRoot, 'src/../../outside/secret.txt'),
        throwsA(isA<ClientContainmentViolation>()),
      );
    });

    test('still refuses a sibling that shares the root name as a prefix', () {
      if (noAlias()) return;
      expect(
        () => requireContainedClient(aliasRoot, siblingFile),
        throwsA(isA<ClientContainmentViolation>()),
      );
      expect(
        () => requireContainedClient(realRoot, '../work2/b.txt'),
        throwsA(isA<ClientContainmentViolation>()),
      );
    });

    test('still refuses a symlink inside an aliased root that escapes it', () {
      if (noAlias()) return;
      Link(p.join(realRoot, 'src', 'escape')).createSync(outsideFile);
      expect(
        () => requireContainedClient(aliasRoot, 'src/escape'),
        throwsA(isA<ClientContainmentViolation>()),
      );
      expect(
        () => requireContainedClient(realRoot, p.join(aliasRoot, 'src', 'escape')),
        throwsA(isA<ClientContainmentViolation>()),
      );
    });

    test('findContainingRootClient matches an aliased root and returns it as given', () {
      if (noAlias()) return;
      final roots = [p.join(p.dirname(realRoot), 'outside'), aliasRoot];
      expect(findContainingRootClient(roots, p.join(realRoot, 'src', 'a.dart')), aliasRoot);
      expect(findContainingRootClient([aliasRoot], siblingFile), isNull);
    });
  });

  // The string comparison that runs after canonicalization, exercised under Windows rules on
  // any host. It deliberately does NOT treat `RUNNER~1` as `runneradmin`: equating 8.3 and long
  // names is the canonicalization step's job (resolveSymbolicLinksSync returns the long form on
  // Windows), which is exactly why requireContainedClient must canonicalize both sides.
  group('isSameOrWithinCanonical', () {
    bool windows(String root, String candidate) =>
        isSameOrWithinCanonical(root, candidate, context: p.windows, caseInsensitive: true);
    bool posix(String root, String candidate) =>
        isSameOrWithinCanonical(root, candidate, context: p.posix, caseInsensitive: false);

    test('Windows: ignores case and separator style', () {
      expect(windows(r'C:\Users\runneradmin\Temp\ws', 'c:/users/RUNNERADMIN/temp/ws/src/a.dart'), isTrue);
      expect(windows(r'C:\ws', r'c:\WS'), isTrue);
    });

    test('Windows: refuses a sibling prefix and .. escapes', () {
      expect(windows(r'C:\ws', r'C:\ws2\a.dart'), isFalse);
      expect(windows(r'C:\ws', r'C:\ws\..\secret'), isFalse);
    });

    test('Windows: a drive root contains its children without a doubled separator', () {
      expect(windows(r'C:\', r'C:\Windows'), isTrue);
      expect(windows(r'C:\', r'D:\data'), isFalse);
    });

    test('Windows: an 8.3 short name is not equated with its long form by comparison alone', () {
      expect(windows(r'C:\Users\RUNNER~1\Temp', r'C:\Users\runneradmin\Temp\a.dart'), isFalse);
    });

    test('POSIX: case-sensitive, refuses sibling prefixes, handles /', () {
      expect(posix('/work', '/work'), isTrue);
      expect(posix('/work', '/work/src/a.dart'), isTrue);
      expect(posix('/work', '/work2/a.dart'), isFalse);
      expect(posix('/Work', '/work/a.dart'), isFalse);
      expect(posix('/work', '/work/../etc/passwd'), isFalse);
      expect(posix('/', '/etc'), isTrue);
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
