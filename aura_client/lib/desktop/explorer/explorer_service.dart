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
import 'package:aura_client/l10n/l10n.dart';

/// Result of a directory listing.
@immutable
class DirectoryListing {
  const DirectoryListing({
    required this.entries,
    this.error,
    this.isAccessDenied = false,
  });

  final List<FileSystemEntry> entries;
  final String? error;

  /// True when [error] is an access/containment refusal rather than a
  /// transient I/O failure -- the UI remediation is "Grant Access", not "Retry".
  final bool isAccessDenied;
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

/// Windows reports access-denied as Win32 ERROR_ACCESS_DENIED (5), POSIX as
/// EACCES (13). Anything else (not-found, transient I/O failure) keeps the
/// generic retry-only error path.
bool _isAccessDenied(Object e) =>
    e is FileSystemException && (e.osError?.errorCode == 5 || e.osError?.errorCode == 13);

/// Service for exploring workspace files.
class ExplorerService {
  ExplorerService(this._hostdClient, this._workspaceRoot);

  final HostdControlClient _hostdClient;
  final String _workspaceRoot;

  /// The actual absolute path a granted-root check should test [relativePath] against. An empty
  /// `relativePath` means "the workspace root's own directory" (matching this class's `dart:io`
  /// fallback, where `requireContainedClient(_workspaceRoot, '')` also resolves to the root
  /// itself) -- resolving it to [_workspaceRoot] rather than testing the literal empty string is
  /// what lets the workspace root's own top-level listing match a granted root at all.
  /// Subdirectory paths are used as-is: hostd-sourced entries already carry an absolute path (see
  /// `explorer_tree_tile.dart`'s own doc comment on this), so they already start with whichever
  /// granted root covers them.
  String _workspaceGrantCheckPath(String relativePath) => relativePath.isEmpty ? _workspaceRoot : relativePath;

  /// Lists a directory, preferring hostd when available and root is granted.
  ///
  /// [hostConnected] distinguishes two very different reasons a call can find no matching entry
  /// in [grantedRoots]: hostd being unreachable entirely (the D2 exit criteria's sanctioned
  /// "hostd down -> read via dart:io, read-only" fallback), versus hostd being reachable but
  /// simply never having been granted this root (the plan's "AURA does not currently have access
  /// to this folder -- Grant Access" case). Conflating the two -- as this method used to, by
  /// falling back to dart:io silently either way -- means opening a workspace hostd has no grant
  /// for never surfaces that at all, since the client process can usually still read the folder
  /// itself via the OS (Phase 0 desktop audit, 2026-09-25).
  /// [allowLocalFallback] gates the `dart:io` fallback below. It defaults to `true` for the
  /// desktop IDE Explorer, where the client process and the granted root are on the same machine
  /// so reading the path directly is a legitimate (if less-authoritative) substitute when hostd is
  /// unreachable. Callers whose client device is *not* necessarily co-located with the granted
  /// root -- `HostFileBrowserPage`, reachable from any platform pairing with a remote hostd, not
  /// just the desktop app -- must pass `false`: falling back to `dart:io` there would read the
  /// *client device's* filesystem at a path that only means anything on the remote host, which is
  /// wrong rather than merely degraded (2026-09-25 audit follow-up, collapsing the two pages'
  /// previously-duplicated listing/reading logic onto this service).
  Future<DirectoryListing> listDirectory({
    required String relativePath,
    List<String> grantedRoots = const [],
    required bool hostConnected,
    bool allowLocalFallback = true,
  }) async {
    // Check if path is under a granted root for hostd. `relativePath` is '' for the workspace
    // root's own listing (see `_workspaceGrantCheckPath`'s doc comment) -- '' never starts with
    // any non-empty granted root, so without resolving it to the actual workspace root first, a
    // properly-granted workspace's *own* top-level listing could never match hostd at all, making
    // the `hostConnected` branch below misfire "access required" on every workspace regardless of
    // grant status.
    final hostdRoot = grantedRoots.firstWhere(
      (root) => _workspaceGrantCheckPath(relativePath).startsWith(root) || root == '',
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
        // A *known-granted* root call failing is a transient/other problem, not a permissions
        // one -- fall back to dart:io same as always, unless the caller has ruled that fallback
        // out entirely (see allowLocalFallback's doc comment).
        if (!allowLocalFallback) {
          return DirectoryListing(entries: [], error: e.toString());
        }
      }
    } else if (hostConnected) {
      // currentL10n: no BuildContext here, and the listing is shown as soon as it returns.
      return DirectoryListing(
        entries: const [],
        error: currentL10n.explorerOutsideGrantedRoots,
        isAccessDenied: true,
      );
    }

    if (!allowLocalFallback) {
      return DirectoryListing(entries: const [], error: currentL10n.explorerHostUnreachable);
    }

    // Fallback to dart:io -- hostd is offline, or a granted-root hostd call above failed
    // transiently.
    try {
      final resolvedPath = requireContainedClient(_workspaceRoot, relativePath);
      final dir = Directory(resolvedPath);
      if (!dir.existsSync()) {
        return DirectoryListing(entries: [], error: currentL10n.explorerDirectoryNotFound);
      }
      final entries = dir.listSync()
          .map((e) => FileSystemEntry.fromIO(e, _workspaceRoot))
          .toList();
      return DirectoryListing(entries: entries);
    } on ClientContainmentViolation catch (e) {
      return DirectoryListing(entries: [], error: e.message, isAccessDenied: true);
    } catch (e) {
      return DirectoryListing(entries: [], error: e.toString(), isAccessDenied: _isAccessDenied(e));
    }
  }

  /// Reads a file, preferring hostd when available. See [listDirectory]'s doc comment for what
  /// [hostConnected] is for -- the same ungranted-root-vs-hostd-down distinction applies here,
  /// though this method's `String?` return can't carry the richer [DirectoryListing.isAccessDenied]
  /// signal; an ungranted root and a genuine read failure both come back as `null`.
  Future<String?> readFile({
    required String relativePath,
    List<String> grantedRoots = const [],
    required bool hostConnected,
    bool allowLocalFallback = true,
  }) async {
    final hostdRoot = grantedRoots.firstWhere(
      (root) => _workspaceGrantCheckPath(relativePath).startsWith(root) || root == '',
      orElse: () => '',
    );

    if (hostdRoot.isNotEmpty) {
      try {
        final result = await _hostdClient.readFile(root: hostdRoot, path: relativePath);
        return result['content'] as String?;
      } catch (_) {
        // Fall back to dart:io, unless the caller ruled that out (see listDirectory's doc
        // comment on allowLocalFallback -- same reasoning applies here).
        if (!allowLocalFallback) return null;
      }
    } else if (hostConnected) {
      return null;
    }

    if (!allowLocalFallback) return null;

    try {
      final resolvedPath = requireContainedClient(_workspaceRoot, relativePath);
      // Awaited on purpose: returning the Future unawaited hands it to the caller *outside* this
      // try, so a read that fails (missing file, permission denied) escaped as an exception
      // instead of taking the `return null` below, which is what every caller expects.
      return await File(resolvedPath).readAsString();
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