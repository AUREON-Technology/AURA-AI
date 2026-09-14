/// Shared explorer service for Desktop IDE.
///
/// Provides file listing/reading for both the IDE explorer and HostFileBrowserPage.
library;

import 'dart:io';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:meta/meta.dart';
import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/core/security/path_containment.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

/// Result of a directory listing.
@immutable
class DirectoryListing {
  const DirectoryListing({
    required this.entries,
    this.error,
  });

  final List<FileSystemEntry> entries;
  final String? error;
}

/// File system entry for the explorer.
@immutable
class FileSystemEntry {
  const FileSystemEntry({
    required this.name,
    required this.path,
    required this.isDirectory,
    required this.size,
    required this.modifiedAt,
    this.extension,
  });

  final String name;
  final String path;
  final bool isDirectory;
  final int size;
  final DateTime modifiedAt;
  final String? extension;

  factory FileSystemEntry.fromHostd(Map<String, dynamic> json, String rootPath) {
    final name = json['name'] as String;
    final isDir = json['is_dir'] as bool;
    final path = isDir ? '$rootPath/$name' : '$rootPath/$name';
    return FileSystemEntry(
      name: name,
      path: path,
      isDirectory: isDir,
      size: (json['size'] as num?)?.toInt() ?? 0,
      modifiedAt: DateTime.tryParse(json['modified'] as String? ?? '') ?? DateTime.now(),
      extension: isDir ? null : name.split('.').last.toLowerCase(),
    );
  }

  factory FileSystemEntry.fromIO(FileSystemEntity entity, String rootPath) {
    final stat = entity.statSync();
    final name = entity.path.split(Platform.pathSeparator).last;
    final relativePath = entity.path.replaceFirst('$rootPath/', '');
    return FileSystemEntry(
      name: name,
      path: relativePath,
      isDirectory: entity is Directory,
      size: stat.size,
      modifiedAt: stat.modified,
      extension: entity is File ? name.split('.').last.toLowerCase() : null,
    );
  }
}

/// Service for exploring workspace files.
class ExplorerService {
  ExplorerService(this._hostdClient, this._workspaceRoot);

  final HostdControlClient _hostdClient;
  final String _workspaceRoot;

  /// Lists a directory, preferring hostd when available and root is granted.
  Future<DirectoryListing> listDirectory({
    required String relativePath,
    List<String> grantedRoots = const [],
  }) async {
    // Check if path is under a granted root for hostd
    final hostdRoot = grantedRoots.firstWhere(
      (root) => relativePath.startsWith(root) || root == '',
      orElse: () => '',
    );

    if (hostdRoot.isNotEmpty) {
      try {
        final result = await _hostdClient.listDirectory(root: hostdRoot, path: relativePath);
        final entries = (result['entries'] as List)
            .map((e) => FileSystemEntry.fromHostd(e as Map<String, dynamic>, hostdRoot))
            .toList();
        return DirectoryListing(entries: entries);
      } catch (e) {
        // Fall back to dart:io
      }
    }

    // Fallback to dart:io
    try {
      final resolvedPath = requireContainedClient(_workspaceRoot, relativePath);
      final dir = Directory(resolvedPath);
      if (!dir.existsSync()) {
        return DirectoryListing(entries: [], error: 'Directory not found');
      }
      final entries = dir.listSync()
          .map((e) => FileSystemEntry.fromIO(e, _workspaceRoot))
          .toList();
      return DirectoryListing(entries: entries);
    } on ClientContainmentViolation catch (e) {
      return DirectoryListing(entries: [], error: e.message);
    } catch (e) {
      return DirectoryListing(entries: [], error: e.toString());
    }
  }

  /// Reads a file, preferring hostd when available.
  Future<String?> readFile({
    required String relativePath,
    List<String> grantedRoots = const [],
  }) async {
    final hostdRoot = grantedRoots.firstWhere(
      (root) => relativePath.startsWith(root) || root == '',
      orElse: () => '',
    );

    if (hostdRoot.isNotEmpty) {
      try {
        final result = await _hostdClient.readFile(root: hostdRoot, path: relativePath);
        return result['content'] as String?;
      } catch (_) {
        // Fall back to dart:io
      }
    }

    try {
      final resolvedPath = requireContainedClient(_workspaceRoot, relativePath);
      return File(resolvedPath).readAsString();
    } on ClientContainmentViolation {
      return null;
    } catch (_) {
      return null;
    }
  }
}

/// Riverpod provider for explorer service.
final explorerServiceProvider = Provider<ExplorerService>((ref) {
  final hostdClient = ref.watch(hostdControlClientProvider);
  final workspaceRoot = ref.watch(workspaceNotifierProvider).activeWorkspace?.rootPath ?? '';
  return ExplorerService(hostdClient, workspaceRoot);
});