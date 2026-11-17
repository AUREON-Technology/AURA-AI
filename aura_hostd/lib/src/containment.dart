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
///
/// Filesystem roots themselves (`/`, every Windows drive root, every UNC share root) are not
/// listed here: there is no finite list of them, so [isFilesystemRoot] refuses them by shape.
final List<String> _rootDenylist = _buildRootDenylist();

List<String> _buildRootDenylist() {
  final home = _homeDirectory();
  final entries = <String>{
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
    // macOS: /etc and /var are symlinks into /private, and a root is compared after symlink
    // resolution, so without these two a grant of /etc would resolve past the entries above.
    '/private/etc',
    '/private/var',
    r'C:\Windows',
    r'C:\Program Files',
    r'C:\Program Files (x86)',
  };
  if (Platform.isWindows) {
    // Windows is not always on C:. These name wherever this machine actually put it.
    for (final name in ['SystemRoot', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432']) {
      final value = Platform.environment[name];
      if (value != null && value.isNotEmpty) entries.add(value);
    }
  }
  if (home != null) {
    // The home directory itself is denied (too broad to be a meaningful boundary); a project
    // folder *inside* home is fine and is exactly the common case.
    entries.add(home);
  }
  return entries.map(_normalizeForCompare).toList(growable: false);
}

/// Whether [path] is a filesystem root itself rather than something inside one: `/` on POSIX; on
/// Windows any drive root (`C:\`, `D:\`, ... -- not just `C:\`), a bare drive (`D:`), the
/// current-drive root `\`, and a UNC server or share root (`\\server`, `\\server\share`),
/// including their `\\?\` / `\\?\UNC\` / `\\.\` long-path spellings.
///
/// [style] defaults to the host platform's; passing it lets the Windows rules be checked on any
/// host. Pure string logic -- touches no filesystem.
bool isFilesystemRoot(String path, {p.Style? style}) {
  final context = style == null ? p.context : p.Context(style: style);
  var candidate = path;
  if (context.style == p.Style.windows) {
    candidate = candidate.replaceAll('/', r'\');
    final lower = candidate.toLowerCase();
    if (lower.startsWith(r'\\?\unc\')) {
      candidate = '\\\\${candidate.substring(8)}';
    } else if (lower.startsWith(r'\\?\') || lower.startsWith(r'\\.\')) {
      candidate = candidate.substring(4);
    }
    if (RegExp(r'^[A-Za-z]:\\*$').hasMatch(candidate)) return true;
  }
  final normalized = context.normalize(candidate);
  return context.isAbsolute(normalized) && context.split(normalized).length == 1;
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

/// Thrown when a call names (or was bound to) a workspace root that has since been revoked --
/// the grant is checked at use, not only when the task was created (audit INT-002).
class RootRevokedError implements Exception {
  RootRevokedError(this.message);
  final String message;

  @override
  String toString() => message;
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

  /// Replaces every grant with [roots], in place, so every holder of this instance sees the
  /// change at once (`WorkspaceStore.live`, audit INT-002).
  void replaceAll(Iterable<WorkspaceRoot> roots) {
    final next = List.of(roots);
    _roots
      ..clear()
      ..addAll(next);
  }

  /// The granted root whose canonical path is exactly [canonicalPath], or `null` if it is no
  /// longer granted. Used to re-read a root's current trust after it was bound to a task.
  WorkspaceRoot? byCanonicalPath(String canonicalPath) {
    for (final root in _roots) {
      if (root.canonicalPath == canonicalPath) return root;
    }
    return null;
  }

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
    if (isFilesystemRoot(canonical)) {
      throw InvalidRootError(
        '$rawPath resolves to $canonical, a filesystem root, which is on the denylist and can '
        'never be a workspace root -- grant a project folder inside it instead',
      );
    }
    final normalized = _normalizeForCompare(canonical);
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

  /// Finds the granted root that contains [rawPath] once fully resolved, or `null` if none does.
  /// Used when a call does not name which root it means (paths are workspace-relative from the
  /// tool layer's point of view) and the daemon must find it among everything granted.
  ///
  /// When granted roots nest, the most specific (longest) containing root wins, whatever the
  /// grant order: trust is per root, and a path under an untrusted `/work/vendor` granted after a
  /// trusted `/work` must not inherit the outer root's trust just because it was granted first.
  WorkspaceRoot? findContainingRoot(String rawPath) {
    WorkspaceRoot? best;
    for (final root in _roots) {
      try {
        requireContained(root, rawPath);
      } on ContainmentViolation {
        continue;
      }
      if (best == null || root.canonicalPath.length > best.canonicalPath.length) {
        best = root;
      }
    }
    return best;
  }
}
