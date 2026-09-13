/// Explorer service for Desktop IDE.
///
/// Lists a workspace's file tree for the sidebar Explorer panel. Uses
/// [WorkspaceFileService] (client-side `dart:io` + containment check) so the
/// explorer works even when the workspace root hasn't been granted to
/// `aura_hostd` yet. A hostd-backed listing (for parity with the Engineer
/// agent's own view of granted roots) can be layered in later without
/// changing this API.
library;

import 'dart:io';

import 'package:path/path.dart' as p;

import 'package:aura_client/desktop/workspace/workspace_file_service.dart';

class ExplorerEntry {
  const ExplorerEntry({
    required this.name,
    required this.relativePath,
    required this.isDirectory,
  });

  final String name;
  final String relativePath;
  final bool isDirectory;
}

class ExplorerService {
  ExplorerService(this.workspaceRoot) : _files = WorkspaceFileService(workspaceRoot);

  final String workspaceRoot;
  final WorkspaceFileService _files;

  /// Lists the immediate children of [relativePath] (workspace-root-relative;
  /// `''` for the root itself), directories first, then alphabetically.
  Future<List<ExplorerEntry>> list(String relativePath) async {
    final entities = await _files.listDirectory(relativePath);
    final entries = entities
        .where((e) => !_isIgnored(p.basename(e.path)))
        .map((e) => ExplorerEntry(
              name: p.basename(e.path),
              relativePath: relativePath.isEmpty
                  ? p.basename(e.path)
                  : p.join(relativePath, p.basename(e.path)),
              isDirectory: e is Directory,
            ))
        .toList();
    entries.sort((a, b) {
      if (a.isDirectory != b.isDirectory) return a.isDirectory ? -1 : 1;
      return a.name.toLowerCase().compareTo(b.name.toLowerCase());
    });
    return entries;
  }

  static const _ignoredNames = {
    '.git',
    'node_modules',
    '.dart_tool',
    'build',
    '.idea',
    '.vscode',
  };

  bool _isIgnored(String name) => _ignoredNames.contains(name);
}
