/// Path containment: the daemon's actual security boundary.
///
/// ADR-011 states the reason this exists in `aura_hostd` and not only on the server: "a confused
/// or compromised backend must not be able to touch the user's whole filesystem." Server-side
/// `ToolPolicy` (aura_backend) answers "is this call authorized at all" -- it cannot answer "is
/// `/etc/passwd` a path this user meant to grant." Only code running on this machine can, so this
/// check is never optional and no consent tier is allowed to bypass it.
///
/// The discipline matches `aura_backend/aura_core/tools/builtin.py::build_file_tools`'s
/// `resolve_within`: resolve symlinks *first*, then check containment against the resolved path.
/// Checking containment before resolving symlinks is the classic bypass -- a symlink inside a
/// granted root pointing outside it would pass a naive prefix check.
library;

import 'dart:io';

import 'package:path/path.dart' as p;

/// Absolute paths that can never be granted as a workspace root, regardless of what the user
/// asks for. This is a hard floor under the user's own consent -- granting one of these as a root
/// would make "contained to the workspace" meaningless, so the daemon refuses at grant time
/// rather than trusting the human not to fat-finger a path.
final List<String> _rootDenylist = _buildRootDenylist();

List<String> _buildRootDenylist() {
  final home = _homeDirectory();
  final entries = <String>{
    '/',
    '/etc',
    '/usr',
    '/bin',
    '/sbin',
    '/boot',
    '/dev',
    '/proc',
    '/sys',
    '/var',
    '/System',
    '/Library',
    r'C:\',
    r'C:\Windows',
    r'C:\Program Files',
    r'C:\Program Files (x86)',
  };
  if (home != null) {
    // The home directory itself is denied (too broad to be a meaningful boundary); a project
    // folder *inside* home is fine and is exactly the common case.
    entries.add(home);
  }
  return entries.map(_normalizeForCompare).toList(growable: false);
}

String? _homeDirectory() {
  if (Platform.isWindows) {
    return Platform.environment['USERPROFILE'];
  }
  return Platform.environment['HOME'];
}

String _normalizeForCompare(String path) {
  final normalized = p.normalize(path);
  return Platform.isWindows ? normalized.toLowerCase() : normalized;
}

class ContainmentViolation implements Exception {
  ContainmentViolation(this.message);
  final String message;

  @override
  String toString() => 'ContainmentViolation: $message';
}

/// Thrown by [WorkspaceRoots.addRoot] when the requested path is denylisted or otherwise unfit
/// to be a workspace root (does not exist, is not a directory, or resolves to a denylisted path
/// through a symlink).
class InvalidRootError implements Exception {
  InvalidRootError(this.message);
  final String message;

  @override
  String toString() => 'InvalidRootError: $message';
}

/// A single granted workspace root. `trusted` controls command policy (see `command_policy.dart`)
/// -- it never controls containment, which is unconditional for every root.
class WorkspaceRoot {
  WorkspaceRoot({required this.canonicalPath, required this.trusted});

  final String canonicalPath;
  final bool trusted;

  WorkspaceRoot copyWith({bool? trusted}) =>
      WorkspaceRoot(canonicalPath: canonicalPath, trusted: trusted ?? this.trusted);

  Map<String, Object?> toJson() => {'path': canonicalPath, 'trusted': trusted};

  static WorkspaceRoot fromJson(Map<String, dynamic> json) => WorkspaceRoot(
        canonicalPath: json['path'] as String,
        trusted: json['trusted'] as bool? ?? false,
      );
}

/// Resolves a symlink chain without requiring the target to exist -- `File.resolveSymbolicLinks`
/// throws on a missing target, which is exactly the case a new file being written needs to
/// tolerate. Resolves the longest existing prefix and reattaches the remainder unresolved (safe:
/// any symlink component within that remainder does not exist yet either, by definition).
String _resolveAsFarAsPossible(String rawPath) {
  var current = p.normalize(p.isAbsolute(rawPath) ? rawPath : p.absolute(rawPath));
  final trailing = <String>[];
  while (true) {
    final type = FileSystemEntity.typeSync(current, followLinks: false);
    if (type != FileSystemEntityType.notFound) {
      final resolved = File(current).resolveSymbolicLinksSync();
      return trailing.isEmpty ? resolved : p.joinAll([resolved, ...trailing.reversed]);
    }
    final parent = p.dirname(current);
    if (parent == current) {
      // Hit the filesystem root without finding anything that exists.
      return p.joinAll([current, ...trailing.reversed]);
    }
    trailing.add(p.basename(current));
    current = parent;
  }
}

/// Holds the set of roots the user has granted and answers the one question that matters:
/// "is this path inside one of them, after resolving symlinks?"
class WorkspaceRoots {
  WorkspaceRoots(Iterable<WorkspaceRoot> initial) : _roots = List.of(initial);

  final List<WorkspaceRoot> _roots;

  List<WorkspaceRoot> get all => List.unmodifiable(_roots);

  /// Validates and adds [rawPath] as a granted root. Throws [InvalidRootError] if the path is
  /// denylisted (directly or through a symlink), does not exist, or is not a directory.
  WorkspaceRoot addRoot(String rawPath, {bool trusted = false}) {
    if (!Directory(rawPath).existsSync()) {
      throw InvalidRootError('$rawPath does not exist or is not a directory');
    }
    final canonical = File(rawPath).resolveSymbolicLinksSync();
    if (!Directory(canonical).existsSync()) {
      throw InvalidRootError('$rawPath does not resolve to a directory');
    }
    final normalized = _normalizeForCompare(canonical);
    // Any filesystem root (/, C:\, E:\, …) is never a meaningful workspace boundary.
    // On Windows, '/' often resolves to the *current* drive root (e.g. E:\), which is
    // not the same string as the hardcoded C:\ denylist entry.
    if (p.dirname(canonical) == canonical) {
      throw InvalidRootError(
        '$rawPath resolves to $canonical, which is a filesystem root and can never be a '
        'workspace root -- grant a project folder inside it instead',
      );
    }
    for (final denied in _rootDenylist) {
      if (normalized == denied) {
        throw InvalidRootError(
          '$rawPath resolves to $canonical, which is on the denylist and can never be a '
          'workspace root -- grant a project folder inside it instead',
        );
      }
    }
    final existingIndex = _roots.indexWhere((r) => r.canonicalPath == canonical);
    final root = WorkspaceRoot(canonicalPath: canonical, trusted: trusted);
    if (existingIndex >= 0) {
      _roots[existingIndex] = root;
    } else {
      _roots.add(root);
    }
    return root;
  }

  bool removeRoot(String rawPath) {
    final canonical = _tryResolve(rawPath) ?? p.normalize(rawPath);
    final before = _roots.length;
    _roots.removeWhere((r) => r.canonicalPath == canonical || r.canonicalPath == p.normalize(rawPath));
    return _roots.length != before;
  }

  WorkspaceRoot? setTrusted(String rawPath, bool trusted) {
    final canonical = _tryResolve(rawPath) ?? p.normalize(rawPath);
    final index = _roots.indexWhere((r) => r.canonicalPath == canonical);
    if (index < 0) return null;
    _roots[index] = _roots[index].copyWith(trusted: trusted);
    return _roots[index];
  }

  String? _tryResolve(String rawPath) {
    try {
      return File(rawPath).resolveSymbolicLinksSync();
    } on FileSystemException {
      return null;
    }
  }

  /// Resolves [rawPath] (relative paths are taken relative to [root]'s canonical path) and
  /// returns its canonical, symlink-resolved absolute form -- but only if that resolved path is
  /// contained within [root]. Throws [ContainmentViolation] otherwise. This is the single
  /// enforcement point every filesystem/command RPC handler must call before touching disk.
  String requireContained(WorkspaceRoot root, String rawPath) {
    final candidate = p.isAbsolute(rawPath) ? rawPath : p.join(root.canonicalPath, rawPath);
    final resolved = _resolveAsFarAsPossible(candidate);
    final normalizedResolved = _normalizeForCompare(resolved);
    final normalizedRoot = _normalizeForCompare(root.canonicalPath);
    final isContained = normalizedResolved == normalizedRoot ||
        normalizedResolved.startsWith('$normalizedRoot${p.separator}');
    if (!isContained) {
      throw ContainmentViolation(
        '$rawPath resolves to $resolved, which is outside the granted workspace root '
        '${root.canonicalPath} -- grant that path as its own root to use it',
      );
    }
    return resolved;
  }

  /// Finds the first granted root that contains [rawPath] once fully resolved, or `null` if none
  /// does. Used when a call does not name which root it means (paths are workspace-relative from
  /// the tool layer's point of view) and the daemon must find it among everything granted.
  WorkspaceRoot? findContainingRoot(String rawPath) {
    for (final root in _roots) {
      try {
        requireContained(root, rawPath);
        return root;
      } on ContainmentViolation {
        continue;
      }
    }
    return null;
  }
}
