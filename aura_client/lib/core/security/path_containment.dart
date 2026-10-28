/// Path containment utilities for AURA Desktop IDE (client-side UX validation).
///
/// This mirrors the authoritative containment logic in `aura_hostd/lib/src/containment.dart`
/// but runs on the client for UX safety / accidental access prevention only.
///
/// **Security rule:** Client containment = UX validation. Host/backend containment = authoritative boundary.
/// The client never treats its own check as the security authority.
library;

import 'dart:io';
import 'package:meta/meta.dart';
import 'package:path/path.dart' as p;

/// Normalizes a path for comparison (case-insensitive on Windows).
String _normalizeForCompare(String path) => normalizePathForCompare(
      path,
      context: p.context,
      caseInsensitive: Platform.isWindows,
    );

/// Normalizes [path] under [context] (collapsing `.`/`..` and, for a Windows context, turning
/// `/` into `\`), lower-cased when [caseInsensitive]. Parameterized only so tests can exercise
/// the Windows rules on a Linux runner; production goes through [_normalizeForCompare].
@visibleForTesting
String normalizePathForCompare(
  String path, {
  required p.Context context,
  required bool caseInsensitive,
}) {
  final normalized = context.normalize(path);
  return caseInsensitive ? normalized.toLowerCase() : normalized;
}

/// True if [candidate] is [root] itself or lies beneath it. Both arguments must already be
/// canonical (symlinks resolved, long-form on Windows) -- this only compares strings, after
/// normalizing separators, `.`/`..` and (when [caseInsensitive]) case. The separator suffix is
/// what refuses sibling-prefix tricks (`/work` must not contain `/work2`); a root that already
/// ends in a separator (a drive or filesystem root) is not given a second one.
@visibleForTesting
bool isSameOrWithinCanonical(
  String root,
  String candidate, {
  required p.Context context,
  required bool caseInsensitive,
}) {
  final normalizedRoot = normalizePathForCompare(root, context: context, caseInsensitive: caseInsensitive);
  final normalizedCandidate =
      normalizePathForCompare(candidate, context: context, caseInsensitive: caseInsensitive);
  if (normalizedCandidate == normalizedRoot) return true;
  final prefix =
      normalizedRoot.endsWith(context.separator) ? normalizedRoot : '$normalizedRoot${context.separator}';
  return normalizedCandidate.startsWith(prefix);
}

/// Absolute paths that can never be granted as a workspace root.
/// Mirrors `aura_hostd/lib/src/containment.dart`'s `_rootDenylist`.
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

/// Resolves a path as far as possible (handles non-existent trailing components).
/// Mirrors `aura_hostd/lib/src/containment.dart`'s `_resolveAsFarAsPossible`.
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
      return p.joinAll([current, ...trailing.reversed]);
    }
    trailing.add(p.basename(current));
    current = parent;
  }
}

/// Thrown when a path is outside the granted workspace root (client-side UX check).
class ClientContainmentViolation implements Exception {
  ClientContainmentViolation(this.message);
  final String message;

  @override
  String toString() => 'ClientContainmentViolation: $message';
}

/// Thrown when a path is denylisted or otherwise unfit as a workspace root.
class ClientInvalidRootError implements Exception {
  ClientInvalidRootError(this.message);
  final String message;

  @override
  String toString() => 'ClientInvalidRootError: $message';
}

/// Validates a workspace root candidate (client-side UX check).
///
/// Throws [ClientInvalidRootError] if the path is denylisted, does not exist,
/// or is not a directory. Does not mutate state — pure validation.
void validateWorkspaceRoot(String rawPath) {
  if (!Directory(rawPath).existsSync()) {
    throw ClientInvalidRootError('$rawPath does not exist or is not a directory');
  }
  final canonical = File(rawPath).resolveSymbolicLinksSync();
  if (!Directory(canonical).existsSync()) {
    throw ClientInvalidRootError('$rawPath does not resolve to a directory');
  }
  final normalized = _normalizeForCompare(canonical);
  for (final denied in _rootDenylist) {
    if (normalized == denied) {
      throw ClientInvalidRootError(
        '$rawPath resolves to $canonical, which is on the denylist and can never be a '
        'workspace root — grant a project folder inside it instead',
      );
    }
  }
}

/// Checks if a path is contained within a workspace root (client-side UX check).
///
/// Returns the resolved, canonical path if contained. Throws [ClientContainmentViolation] otherwise.
/// This is the single enforcement point for client-side filesystem operations.
///
/// [rootPath] may be any spelling of the root -- callers pass the workspace path as the user
/// picked it, which need not be canonical. Both sides are canonicalized the same way before they
/// are compared: the candidate is resolved (symlinks, and on Windows the long form of 8.3 short
/// names such as `RUNNER~1`) and so is the root. Comparing a resolved candidate against an
/// unresolved root refused legitimate files whenever the root was reached through an alias --
/// an 8.3 short name on Windows, a symlinked directory anywhere (macOS's `/var` ->
/// `/private/var` included).
String requireContainedClient(String rootPath, String rawPath) {
  final rootCanonical = _resolveAsFarAsPossible(rootPath);
  final candidate = p.isAbsolute(rawPath) ? rawPath : p.join(rootCanonical, rawPath);
  final resolved = _resolveAsFarAsPossible(candidate);
  final isContained = isSameOrWithinCanonical(
    rootCanonical,
    resolved,
    context: p.context,
    caseInsensitive: Platform.isWindows,
  );
  if (!isContained) {
    throw ClientContainmentViolation(
      '$rawPath resolves to $resolved, which is outside the granted workspace root '
      '$rootPath (canonically $rootCanonical) — grant that path as its own root to use it',
    );
  }
  return resolved;
}

/// Finds the first granted root that contains [rawPath] once fully resolved, or `null` if none.
String? findContainingRootClient(List<String> rootCanonicalPaths, String rawPath) {
  for (final root in rootCanonicalPaths) {
    try {
      requireContainedClient(root, rawPath);
      return root;
    } on ClientContainmentViolation {
      continue;
    }
  }
  return null;
}

/// Test vectors shared between client and hostd containment logic.
///
/// These MUST pass in both `aura_client` and `aura_hostd` test suites to prevent drift.
const List<ContainmentTestVector> containmentTestVectors = [
  // Basic containment
  ContainmentTestVector(
    name: 'file inside workspace',
    root: 'workspace',
    path: 'workspace/src/a.dart',
    expected: ContainmentResult.allow,
  ),
  ContainmentTestVector(
    name: 'file in subdirectory',
    root: 'workspace',
    path: 'workspace/src/deep/nested/file.dart',
    expected: ContainmentResult.allow,
  ),
  ContainmentTestVector(
    name: 'workspace root itself',
    root: 'workspace',
    path: 'workspace',
    expected: ContainmentResult.allow,
  ),

  // Path traversal attacks
  ContainmentTestVector(
    name: 'parent traversal',
    root: 'workspace',
    path: 'workspace/../secret',
    expected: ContainmentResult.deny,
  ),
  ContainmentTestVector(
    name: 'double parent traversal',
    root: 'workspace/a',
    path: 'workspace/a/../../secret',
    expected: ContainmentResult.deny,
  ),
  ContainmentTestVector(
    name: 'traversal with filename',
    root: 'workspace',
    path: 'workspace/src/../../etc/passwd',
    expected: ContainmentResult.deny,
  ),

  // Symlink escapes (these require actual symlinks to test fully;
  // the resolution logic handles them but test setup needs real FS)
  ContainmentTestVector(
    name: 'symlink to outside (conceptual)',
    root: 'workspace',
    path: 'workspace/link → /etc',
    expected: ContainmentResult.deny,
    requiresSymlink: true,
  ),
  ContainmentTestVector(
    name: 'symlink inside subdir to outside',
    root: 'workspace',
    path: 'workspace/src/link → /etc/passwd',
    expected: ContainmentResult.deny,
    requiresSymlink: true,
  ),

  // Absolute paths
  ContainmentTestVector(
    name: 'absolute path inside root',
    root: 'workspace',
    path: '/abs/path/to/workspace/src/file.dart',
    expected: ContainmentResult.allow,
    note: 'Only if /abs/path/to/workspace is the canonical root',
  ),
  ContainmentTestVector(
    name: 'absolute path outside root',
    root: 'workspace',
    path: '/etc/passwd',
    expected: ContainmentResult.deny,
  ),

  // Denylisted roots
  ContainmentTestVector(
    name: 'root is home directory',
    root: 'home',
    path: 'home/secret.txt',
    expected: ContainmentResult.denyRoot,
    note: 'Home directory itself is denylisted as a root',
  ),
  ContainmentTestVector(
    name: 'root is system directory',
    root: '/etc',
    path: '/etc/passwd',
    expected: ContainmentResult.denyRoot,
  ),
  ContainmentTestVector(
    name: 'root is C:\\ on Windows',
    root: r'C:\',
    path: r'C:\Windows\System32',
    expected: ContainmentResult.denyRoot,
  ),

  // Case sensitivity (Windows)
  ContainmentTestVector(
    name: 'case mismatch on Windows (should allow)',
    root: 'WORKSPACE',
    path: 'workspace/src/file.dart',
    expected: ContainmentResult.allow,
    platform: 'windows',
  ),
  ContainmentTestVector(
    name: 'case mismatch on Linux (should deny if different)',
    root: 'WORKSPACE',
    path: 'workspace/src/file.dart',
    expected: ContainmentResult.deny,
    platform: 'linux',
  ),

  // Edge cases
  ContainmentTestVector(
    name: 'empty relative path',
    root: 'workspace',
    path: '',
    expected: ContainmentResult.allow,
  ),
  ContainmentTestVector(
    name: 'dot path',
    root: 'workspace',
    path: '.',
    expected: ContainmentResult.allow,
  ),
  ContainmentTestVector(
    name: 'dot-dot path (at root)',
    root: 'workspace',
    path: '..',
    expected: ContainmentResult.deny,
  ),
];

enum ContainmentResult { allow, deny, denyRoot }

class ContainmentTestVector {
  const ContainmentTestVector({
    required this.name,
    required this.root,
    required this.path,
    required this.expected,
    this.requiresSymlink = false,
    this.platform,
    this.note,
  });

  final String name;
  final String root;
  final String path;
  final ContainmentResult expected;
  final bool requiresSymlink;
  final String? platform; // 'windows', 'linux', 'macos', or null for all
  final String? note;
}