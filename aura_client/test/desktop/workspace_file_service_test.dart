/// Coverage for the Explorer context menu's file-management operations (Phase 0 desktop audit,
/// 2026-09-25): rename, delete, and create, added to `WorkspaceFileService` since these are
/// human-initiated writes that go straight through `dart:io` (containment-checked), the same way
/// the existing `readFile`/`writeFile` already do -- no hostd grant involved.
import 'dart:io';

import 'package:aura_client/core/security/path_containment.dart';
import 'package:aura_client/desktop/workspace/workspace_file_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path/path.dart' as p;

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late Directory workspaceDir;
  late String workspaceRoot;
  late WorkspaceFileService service;

  setUp(() {
    workspaceDir = Directory.systemTemp.createTempSync('aura_client_workspace_file_service_test_');
    workspaceRoot = workspaceDir.resolveSymbolicLinksSync();
    service = WorkspaceFileService(workspaceRoot);
  });

  tearDown(() {
    workspaceDir.deleteSync(recursive: true);
  });

  group('rename', () {
    test('renames a file in place, preserving its content', () async {
      File(p.join(workspaceRoot, 'old.txt')).writeAsStringSync('hello');

      await service.rename(
        p.join(workspaceRoot, 'old.txt'),
        p.join(workspaceRoot, 'new.txt'),
        isDirectory: false,
      );

      expect(File(p.join(workspaceRoot, 'old.txt')).existsSync(), isFalse);
      expect(File(p.join(workspaceRoot, 'new.txt')).readAsStringSync(), 'hello');
    });

    test('renames a directory, moving its contents with it', () async {
      Directory(p.join(workspaceRoot, 'old_dir')).createSync();
      File(p.join(workspaceRoot, 'old_dir', 'inner.txt')).writeAsStringSync('nested');

      await service.rename(
        p.join(workspaceRoot, 'old_dir'),
        p.join(workspaceRoot, 'new_dir'),
        isDirectory: true,
      );

      expect(Directory(p.join(workspaceRoot, 'old_dir')).existsSync(), isFalse);
      expect(File(p.join(workspaceRoot, 'new_dir', 'inner.txt')).readAsStringSync(), 'nested');
    });

    test('refuses to rename to a path outside the workspace root', () async {
      File(p.join(workspaceRoot, 'a.txt')).writeAsStringSync('hello');
      final outside = p.join(workspaceDir.parent.path, 'escaped.txt');

      expect(
        () => service.rename(p.join(workspaceRoot, 'a.txt'), outside, isDirectory: false),
        throwsA(isA<ClientContainmentViolation>()),
      );
      expect(File(p.join(workspaceRoot, 'a.txt')).existsSync(), isTrue);
    });
  });

  group('delete', () {
    test('deletes a file', () async {
      File(p.join(workspaceRoot, 'a.txt')).writeAsStringSync('hello');

      await service.delete(p.join(workspaceRoot, 'a.txt'), isDirectory: false);

      expect(File(p.join(workspaceRoot, 'a.txt')).existsSync(), isFalse);
    });

    test('deletes a directory recursively', () async {
      Directory(p.join(workspaceRoot, 'dir')).createSync();
      File(p.join(workspaceRoot, 'dir', 'inner.txt')).writeAsStringSync('nested');

      await service.delete(p.join(workspaceRoot, 'dir'), isDirectory: true);

      expect(Directory(p.join(workspaceRoot, 'dir')).existsSync(), isFalse);
    });

    test('refuses to delete a path outside the workspace root', () async {
      final outside = File(p.join(workspaceDir.parent.path, 'not_mine.txt'));
      outside.writeAsStringSync('do not touch');
      addTearDown(() => outside.deleteSync());

      expect(
        () => service.delete(outside.path, isDirectory: false),
        throwsA(isA<ClientContainmentViolation>()),
      );
      expect(outside.existsSync(), isTrue);
    });
  });

  group('createFile', () {
    test('creates a new, empty file', () async {
      await service.createFile(p.join(workspaceRoot, 'new.txt'));

      final file = File(p.join(workspaceRoot, 'new.txt'));
      expect(file.existsSync(), isTrue);
      expect(file.readAsStringSync(), isEmpty);
    });

    test('refuses to overwrite a file that already exists', () async {
      File(p.join(workspaceRoot, 'existing.txt')).writeAsStringSync('do not lose this');

      expect(
        () => service.createFile(p.join(workspaceRoot, 'existing.txt')),
        throwsA(isA<FileSystemException>()),
      );
      expect(File(p.join(workspaceRoot, 'existing.txt')).readAsStringSync(), 'do not lose this');
    });
  });

  group('createDirectory', () {
    test('creates a new, empty directory', () async {
      await service.createDirectory(p.join(workspaceRoot, 'new_dir'));

      expect(Directory(p.join(workspaceRoot, 'new_dir')).existsSync(), isTrue);
    });

    test('refuses to create over an existing directory', () async {
      Directory(p.join(workspaceRoot, 'existing_dir')).createSync();

      expect(
        () => service.createDirectory(p.join(workspaceRoot, 'existing_dir')),
        throwsA(isA<FileSystemException>()),
      );
    });
  });
}
