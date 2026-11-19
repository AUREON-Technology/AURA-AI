/// Workspace file service for Desktop IDE.
///
/// Read/write files with client-side containment check (mirrors hostd rules).
/// This is for human IDE operations - agent operations go through hostd WebSocket.
library;

import 'dart:io';
import 'package:path/path.dart' as p;
import 'package:aura_client/core/security/path_containment.dart';

/// Service for file I/O within a workspace root.
class WorkspaceFileService {
  WorkspaceFileService(this.workspaceRoot);

  final String workspaceRoot;

  /// Reads a file within the workspace.
  Future<String> readFile(String relativePath) async {
    final resolvedPath = requireContainedClient(workspaceRoot, relativePath);
    final file = File(resolvedPath);
    if (!file.existsSync()) {
      throw FileSystemException('File not found', resolvedPath);
    }
    return file.readAsString();
  }

  /// Whether a file exists at [relativePath] within the workspace.
  bool fileExists(String relativePath) {
    final resolvedPath = requireContainedClient(workspaceRoot, relativePath);
    return File(resolvedPath).existsSync();
  }

  /// Writes a file within the workspace.
  ///
  /// The parent folder must already exist. Saving used to create it (`recursive: true`), which
  /// meant a file whose folder had been deleted or renamed outside the editor quietly brought the
  /// old folder back with one file in it; a missing folder is now an error the caller shows.
  Future<void> writeFile(String relativePath, String content) async {
    final resolvedPath = requireContainedClient(workspaceRoot, relativePath);
    await File(resolvedPath).writeAsString(content);
  }

  /// Renames or moves a file or directory within the workspace. [isDirectory] picks the right
  /// `dart:io` entity type -- [File] and [Directory] each declare their own `rename`, with no
  /// shared method on the [FileSystemEntity] base class to call generically. Both endpoints are
  /// containment-checked, so this can't be used to move something out of the workspace root.
  Future<void> rename(String oldRelativePath, String newRelativePath, {required bool isDirectory}) async {
    final oldResolved = requireContainedClient(workspaceRoot, oldRelativePath);
    final newResolved = requireContainedClient(workspaceRoot, newRelativePath);
    // `File.rename` / `Directory.rename` replace an existing file on both Windows and POSIX, so
    // renaming `a.txt` to `b.txt` used to delete `b.txt` without a word. Refuse instead. The one
    // exception is a path that is the same entry once resolved (a case-only rename on Windows
    // resolves to the existing spelling), which is not a collision.
    final sameEntry = oldResolved == newResolved ||
        (Platform.isWindows && oldResolved.toLowerCase() == newResolved.toLowerCase());
    if (!sameEntry && FileSystemEntity.typeSync(newResolved, followLinks: false) != FileSystemEntityType.notFound) {
      throw FileSystemException('A file or folder with that name already exists', newResolved);
    }
    if (isDirectory) {
      await Directory(oldResolved).rename(newResolved);
    } else {
      await File(oldResolved).rename(newResolved);
    }
  }

  /// Deletes a file or directory within the workspace. A directory is deleted recursively --
  /// the caller (the Explorer's context menu) is responsible for confirming this with the human
  /// first, since it can't be undone.
  Future<void> delete(String relativePath, {required bool isDirectory}) async {
    final resolvedPath = requireContainedClient(workspaceRoot, relativePath);
    if (isDirectory) {
      await Directory(resolvedPath).delete(recursive: true);
    } else {
      await File(resolvedPath).delete();
    }
  }

  /// Creates a new, empty file within the workspace. Refuses to overwrite an existing file
  /// (`exclusive: true`) rather than silently truncating something the human didn't mean to touch.
  Future<void> createFile(String relativePath) async {
    final resolvedPath = requireContainedClient(workspaceRoot, relativePath);
    if (File(resolvedPath).existsSync() || Directory(resolvedPath).existsSync()) {
      throw FileSystemException('Something already exists at this path', resolvedPath);
    }
    await File(resolvedPath).create(exclusive: true);
  }

  /// Creates a new, empty directory within the workspace.
  Future<void> createDirectory(String relativePath) async {
    final resolvedPath = requireContainedClient(workspaceRoot, relativePath);
    if (File(resolvedPath).existsSync() || Directory(resolvedPath).existsSync()) {
      throw FileSystemException('Something already exists at this path', resolvedPath);
    }
    await Directory(resolvedPath).create();
  }

  /// Checks if a file exists.
  Future<bool> exists(String relativePath) async {
    try {
      final resolvedPath = requireContainedClient(workspaceRoot, relativePath);
      return File(resolvedPath).existsSync();
    } on ClientContainmentViolation {
      return false;
    }
  }

  /// Lists files in a directory within the workspace.
  Future<List<FileSystemEntity>> listDirectory(String relativePath) async {
    final resolvedPath = requireContainedClient(workspaceRoot, relativePath);
    final dir = Directory(resolvedPath);
    if (!dir.existsSync()) {
      throw FileSystemException('Directory not found', resolvedPath);
    }
    return dir.listSync();
  }

  /// Validates a workspace root.
  void validateRoot() {
    validateWorkspaceRoot(workspaceRoot);
  }
}

/// Recursively walks a directory tree for quick open.
Future<List<String>> walkWorkspaceFiles(
  String workspaceRoot, {
  List<String> ignorePatterns = const ['.git', 'node_modules', '.dart_tool', 'build', '.idea', '.vscode'],
  int maxFiles = 5000,
}) async {
  final results = <String>[];
  await _walkDirectory(
    Directory(workspaceRoot),
    workspaceRoot,
    results,
    ignorePatterns: ignorePatterns,
    maxFiles: maxFiles,
  );
  return results;
}

Future<void> _walkDirectory(
  Directory dir,
  String workspaceRoot,
  List<String> results, {
  required List<String> ignorePatterns,
  required int maxFiles,
}) async {
  if (results.length >= maxFiles) return;

  final entities = dir.listSync();
  for (final entity in entities) {
    if (results.length >= maxFiles) break;

    final basename = p.basename(entity.path);
    if (ignorePatterns.contains(basename)) continue;

    try {
      final relativePath = _getRelativePath(workspaceRoot, entity.path);
      if (relativePath == null) continue;

      if (entity is File) {
        results.add(relativePath);
      } else if (entity is Directory) {
        await _walkDirectory(
          entity,
          workspaceRoot,
          results,
          ignorePatterns: ignorePatterns,
          maxFiles: maxFiles,
        );
      }
    } catch (_) {
      // Skip files that cause errors (symlinks, permission issues, etc.)
    }
  }
}

String? _getRelativePath(String workspaceRoot, String absolutePath) {
  try {
    final normalizedRoot = p.normalize(workspaceRoot);
    final normalizedPath = p.normalize(absolutePath);
    if (!normalizedPath.startsWith(normalizedRoot)) return null;
    return p.relative(normalizedPath, from: normalizedRoot);
  } catch (_) {
    return null;
  }
}