/// Direct, local git execution for the Desktop IDE's Source Control panel (D5,
/// `docs/architecture/015-desktop-ide.md`). Wraps `git` as a subprocess against a workspace
/// root's real checkout -- there is no local git execution anywhere else in this daemon; every
/// git tool call the Engineer agent makes today runs server-side against the Docker sandbox
/// (`aura_backend/aura_core/tools/coding.py`), which this class is deliberately independent of.
///
/// **Human-only boundary**: this class is never registered as an agent tool and is never
/// reachable through [TaskHandler]/`run_command`/generic shell execution -- it is wired into
/// [ControlServer] as its own `/git/*` route family, with no path from the agent-facing RPC
/// surface into it (D5 plan design decision 1). A write here is only ever the direct result of a
/// human clicking a button in this app's own Git panel; that click is the consent, which is why
/// these routes bypass both `root.trusted` and D5's interactive approval queue
/// (`approval_queue.dart`) entirely. Do not add a path from [TaskHandler] to this class without
/// re-reading that decision first.
///
/// No containment logic of its own -- callers (`control_server.dart`'s `/git/*` handlers) resolve
/// and contain every path via [WorkspaceRoots.requireContained] before it ever reaches a method
/// here, exactly like every other filesystem-touching route in this daemon. File arguments are
/// always passed as separate argv elements after a `--` separator, never interpolated into a
/// shell string.
library;

import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;

/// Bounds how much of a diff this service ever holds in memory or returns, matching
/// `task_handler.dart`'s `_maxOutputCapChars` / `control_server.dart`'s `_maxReadBytes`
/// discipline -- a very large diff (a vendored-file commit) should not grow this process's
/// memory or the response body unboundedly.
const _maxDiffCapChars = 500000;

enum GitDiffScope { workingTree, staged }

class GitNotARepositoryError implements Exception {
  GitNotARepositoryError(this.message);
  final String message;
  @override
  String toString() => message;
}

class GitCommandError implements Exception {
  GitCommandError(this.message);
  final String message;
  @override
  String toString() => message;
}

class GitDecodeError implements Exception {
  GitDecodeError(this.message);
  final String message;
  @override
  String toString() => message;
}

class _GitResult {
  _GitResult({required this.exitCode, required this.stdout, required this.stderr});
  final int exitCode;
  final String stdout;
  final String stderr;
}

class GitService {
  Future<Map<String, Object?>> status(String cwd) async {
    final result = await _runOk(cwd, ['-c', 'core.quotepath=false', 'status', '--short', '--branch']);
    final lines = const LineSplitter().convert(result.stdout);
    if (lines.isEmpty) return {'branch': null, 'ahead': 0, 'behind': 0, 'upstream': null, 'files': const []};

    final header = lines.first.startsWith('## ') ? lines.first.substring(3) : lines.first;
    final parsed = _parseStatusHeader(header);

    final files = <Map<String, Object?>>[];
    for (final line in lines.skip(1)) {
      if (line.length < 3) continue;
      final indexStatus = line[0];
      final worktreeStatus = line[1];
      var rest = line.substring(3);
      String? renamedFrom;
      final arrow = rest.indexOf(' -> ');
      if (arrow >= 0) {
        renamedFrom = rest.substring(0, arrow);
        rest = rest.substring(arrow + 4);
      }
      files.add({
        'path': rest,
        if (renamedFrom != null) 'renamed_from': renamedFrom,
        'index_status': indexStatus,
        'worktree_status': worktreeStatus,
        'staged': indexStatus != ' ' && indexStatus != '?',
      });
    }

    return {
      'branch': parsed.branch,
      'upstream': parsed.upstream,
      'ahead': parsed.ahead,
      'behind': parsed.behind,
      'files': files,
    };
  }

  Future<Map<String, Object?>> diff(String cwd, {String? file, required GitDiffScope scope}) async {
    final result = await _runOk(cwd, [
      'diff',
      if (scope == GitDiffScope.staged) '--cached',
      if (file != null) ...['--', file],
    ]);
    final truncated = result.stdout.length > _maxDiffCapChars;
    final text = truncated ? result.stdout.substring(0, _maxDiffCapChars) : result.stdout;
    return {'diff': text, 'truncated': truncated};
  }

  Future<Map<String, Object?>> log(String cwd, {int limit = 30}) async {
    // Unit-separator (\x1f) delimited so a commit subject containing arbitrary text (including a
    // literal pipe or comma) can't break parsing -- matches this file's own containment
    // discipline of never trusting command output to be shell-safe.
    final result = await _run(cwd, ['log', '-n', '$limit', '--format=%H%x1f%an%x1f%aI%x1f%s']);
    if (result.exitCode != 0) {
      if (_looksLikeNotARepo(result.stderr)) {
        throw GitNotARepositoryError('not a git repository');
      }
      if (result.stderr.contains('does not have any commits yet') || result.stdout.isEmpty) {
        return {'commits': const []};
      }
      throw GitCommandError('git log failed: ${result.stderr}');
    }
    final commits = <Map<String, Object?>>[];
    for (final line in const LineSplitter().convert(result.stdout)) {
      if (line.isEmpty) continue;
      final parts = line.split('\x1f');
      if (parts.length != 4) continue;
      commits.add({'sha': parts[0], 'author': parts[1], 'date': parts[2], 'subject': parts[3]});
    }
    return {'commits': commits};
  }

  Future<Map<String, Object?>> stage(String cwd, List<String> paths) async {
    await _runOk(cwd, ['add', '--', ...paths]);
    return {'ok': true};
  }

  Future<Map<String, Object?>> unstage(String cwd, List<String> paths) async {
    await _runOk(cwd, ['restore', '--staged', '--', ...paths]);
    return {'ok': true};
  }

  /// Commits exactly what is already staged -- deliberately never runs `git add -A` first (unlike
  /// the server-side `git_commit` tool), since a human using a staging UI expects the commit to
  /// reflect exactly what they staged, not everything in the working tree.
  Future<Map<String, Object?>> commit(String cwd, String message) async {
    final result = await _run(cwd, ['commit', '-m', message]);
    if (result.exitCode != 0) {
      if (_looksLikeNotARepo(result.stderr)) throw GitNotARepositoryError('not a git repository');
      if (result.stdout.contains('nothing to commit') || result.stderr.contains('nothing to commit')) {
        return {'ok': false, 'reason': 'nothing_staged'};
      }
      throw GitCommandError('git commit failed: ${result.stderr}');
    }
    final sha = await _runOk(cwd, ['rev-parse', 'HEAD']);
    return {'ok': true, 'sha': sha.stdout.trim()};
  }

  Future<Map<String, Object?>> checkout(String cwd, String branch, {bool create = false}) async {
    final result = await _run(cwd, ['checkout', if (create) '-B' else branch, if (create) branch]);
    if (result.exitCode != 0) {
      if (_looksLikeNotARepo(result.stderr)) throw GitNotARepositoryError('not a git repository');
      if (result.stderr.contains('would be overwritten by checkout')) {
        return {
          'ok': false,
          'reason': 'local_changes_would_be_overwritten',
          'affected_files': _parseAffectedFiles(result.stderr),
        };
      }
      throw GitCommandError('git checkout failed: ${result.stderr}');
    }
    return {'ok': true, 'branch': branch};
  }

  /// D8 Part B: creates a `git worktree` for one isolated agent task, sharing [cwd]'s object
  /// store/history -- cheap, real git, and directly mergeable/discardable later (Part C). Lives
  /// under a fixed `.aura-workspaces/<taskId>` subdirectory *inside* the already-granted root, so
  /// no new root-grant UI is ever needed, and is kept out of the human's own `git status` via
  /// `.git/info/exclude` (local-only, never the user's own committed `.gitignore`).
  Future<Map<String, Object?>> createWorktree(
    String cwd, {
    required String taskId,
    String? branchName,
    String? baseBranch,
  }) async {
    final branch = branchName ?? 'aura/task-$taskId';
    final worktreePath = p.join(cwd, '.aura-workspaces', taskId);

    await _excludeFromGitStatus(cwd, '.aura-workspaces/');

    final result = await _run(cwd, [
      'worktree',
      'add',
      '-b',
      branch,
      worktreePath,
      if (baseBranch != null) baseBranch,
    ]);
    if (result.exitCode != 0) {
      if (_looksLikeNotARepo(result.stderr)) throw GitNotARepositoryError('not a git repository');
      throw GitCommandError('git worktree add failed: ${result.stderr}');
    }
    return {'worktree_path': worktreePath, 'branch': branch};
  }

  /// D8/D9: tears down a task's worktree and its branch -- only ever invoked by a human's
  /// explicit "Discard" action (Part C), never by the agent itself. Tolerates the worktree
  /// already being gone (e.g. the daemon crashed mid-task and the directory was manually
  /// cleaned up) rather than treating that as an error.
  Future<Map<String, Object?>> removeWorktree(String cwd, String worktreePath, {required String branch}) async {
    final result = await _run(cwd, ['worktree', 'remove', '--force', worktreePath]);
    if (result.exitCode != 0 && !result.stderr.contains('is not a working tree')) {
      throw GitCommandError('git worktree remove failed: ${result.stderr}');
    }
    final branchResult = await _run(cwd, ['branch', '-D', branch]);
    return {'ok': true, 'branch_deleted': branchResult.exitCode == 0};
  }

  /// Appends [pattern] to `.git/info/exclude` if not already present -- best-effort and silently
  /// skipped on any filesystem error, since this is a cosmetic nicety (keeping the human's own
  /// `git status` clean of the isolation directory), not the actual isolation mechanism itself
  /// (that's the separate worktree/root containment, unaffected by whether this succeeds).
  Future<void> _excludeFromGitStatus(String cwd, String pattern) async {
    try {
      final excludeFile = File(p.join(cwd, '.git', 'info', 'exclude'));
      if (!excludeFile.existsSync()) return;
      final content = excludeFile.readAsStringSync();
      if (content.split('\n').map((line) => line.trim()).contains(pattern)) return;
      final separator = content.isEmpty || content.endsWith('\n') ? '' : '\n';
      excludeFile.writeAsStringSync('$content$separator$pattern\n');
    } on FileSystemException {
      // Non-fatal -- see doc comment above.
    } on FormatException {
      // .git/info/exclude was not valid UTF-8 -- equally non-fatal.
    }
  }

  bool _looksLikeNotARepo(String stderr) => stderr.contains('not a git repository');

  List<String> _parseAffectedFiles(String stderr) {
    final files = <String>[];
    var collecting = false;
    for (final line in const LineSplitter().convert(stderr)) {
      if (line.contains('would be overwritten by checkout')) {
        collecting = true;
        continue;
      }
      if (!collecting) continue;
      final trimmed = line.trim();
      if (trimmed.isEmpty) continue;
      if (line.startsWith('\t') || line.startsWith('    ')) {
        files.add(trimmed);
      } else {
        break;
      }
    }
    return files;
  }

  _StatusHeader _parseStatusHeader(String header) {
    if (header.startsWith('HEAD (no branch)')) {
      return const _StatusHeader(branch: null, upstream: null, ahead: 0, behind: 0);
    }
    if (header.startsWith('No commits yet on ')) {
      return _StatusHeader(branch: header.substring('No commits yet on '.length), upstream: null, ahead: 0, behind: 0);
    }
    final bracketIndex = header.indexOf(' [');
    final beforeBracket = bracketIndex >= 0 ? header.substring(0, bracketIndex) : header;
    final bracketContent = bracketIndex >= 0 ? header.substring(bracketIndex + 2, header.length - 1) : null;

    String branch;
    String? upstream;
    final dots = beforeBracket.indexOf('...');
    if (dots >= 0) {
      branch = beforeBracket.substring(0, dots);
      upstream = beforeBracket.substring(dots + 3);
    } else {
      branch = beforeBracket;
    }

    var ahead = 0;
    var behind = 0;
    if (bracketContent != null) {
      final aheadMatch = RegExp(r'ahead (\d+)').firstMatch(bracketContent);
      final behindMatch = RegExp(r'behind (\d+)').firstMatch(bracketContent);
      if (aheadMatch != null) ahead = int.parse(aheadMatch.group(1)!);
      if (behindMatch != null) behind = int.parse(behindMatch.group(1)!);
    }
    return _StatusHeader(branch: branch, upstream: upstream, ahead: ahead, behind: behind);
  }

  /// Runs [args] and throws on failure: [GitNotARepositoryError] for the "not a git repository"
  /// case (so callers -- `control_server.dart`'s routes -- can map it to a clean `409` the way
  /// `_handleRead` maps a decode failure to `415`), [GitCommandError] otherwise.
  Future<_GitResult> _runOk(String cwd, List<String> args) async {
    final result = await _run(cwd, args);
    if (result.exitCode != 0) {
      if (_looksLikeNotARepo(result.stderr)) throw GitNotARepositoryError('not a git repository');
      throw GitCommandError('git ${args.join(' ')} failed: ${result.stderr}');
    }
    return result;
  }

  Future<_GitResult> _run(String cwd, List<String> args) async {
    final result = await Process.run('git', args, workingDirectory: cwd, stdoutEncoding: null, stderrEncoding: null);
    try {
      return _GitResult(
        exitCode: result.exitCode,
        stdout: utf8.decode(result.stdout as List<int>),
        stderr: utf8.decode(result.stderr as List<int>),
      );
    } on FormatException {
      throw GitDecodeError('git output is not valid UTF-8 text');
    }
  }
}

class _StatusHeader {
  const _StatusHeader({required this.branch, required this.upstream, required this.ahead, required this.behind});
  final String? branch;
  final String? upstream;
  final int ahead;
  final int behind;
}
