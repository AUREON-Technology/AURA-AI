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
/// One narrow, owner-approved exception (WP4, D-13): [isolationState] (a read-only probe) is used
/// on the agent's side to refuse isolation in a folder that cannot hold a worktree, and
/// [initRepository] is reachable from the `init_git` RPC -- but only through `git_init.dart`'s
/// granted-folder checks, and only when the person tapped "Initialize git here" on their phone or
/// the web (`confirmed`, with remote control on). That tap is the consent, as a click here is.
///
/// No containment logic of its own -- callers (`control_server.dart`'s `/git/*` handlers) resolve
/// and contain every path via [WorkspaceRoots.requireContained] before it ever reaches a method
/// here, exactly like every other filesystem-touching route in this daemon. File arguments are
/// always passed as separate argv elements after a `--` separator, never interpolated into a
/// shell string.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;

import 'command_policy.dart' show sharedEmptyHooksDir, untrustedGitGlobalArgs;

/// Bounds how much of a diff this service ever holds in memory or returns, matching
/// `task_handler.dart`'s `_maxOutputCapChars` / `control_server.dart`'s `_maxReadBytes`
/// discipline -- a very large diff (a vendored-file commit) should not grow this process's
/// memory or the response body unboundedly.
const _maxDiffCapChars = 500000;

enum GitDiffScope { workingTree, staged, branchRange }

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

/// WP4 (D-13): whether a folder can hold the Engineer's isolated worktree.
enum GitIsolationState {
  /// A git work tree with at least one commit: `git worktree add` can branch from it.
  ready,

  /// A git work tree with no commit yet: there is nothing to branch from.
  noCommit,

  /// Not inside a git work tree at all.
  notAWorkTree,
}

/// [GitService.initRepository] failed; [detail] is git's own message (stderr).
class GitInitError implements Exception {
  GitInitError(this.message, this.detail);
  final String message;
  final String detail;
  @override
  String toString() => '$message: $detail';
}

class _GitResult {
  _GitResult({required this.exitCode, required this.stdout, required this.stderr});
  final int exitCode;
  final String stdout;
  final String stderr;
}

class GitService {
  /// [networkTimeout] bounds [fetch]/[pull]/[push] (see [_runNetwork]'s doc comment) --
  /// overridable so a test can exercise the timeout-and-kill path deterministically without
  /// waiting out a real 30-second hang.
  ///
  /// [localTimeout] bounds the WP4 git-init steps (`git add -A` of a large folder can take a
  /// while; the backend gives the whole `init_git` call 60 seconds). [environment] is added to
  /// every git process's environment -- a test points `GIT_CONFIG_GLOBAL` at a file of its own to
  /// control which identity git sees; `GIT_TERMINAL_PROMPT=0` always wins.
  ///
  /// [runsRepositoryPrograms] says, per working directory, whether git may run the programs the
  /// repository configures: its hooks (`.git/hooks`, or wherever its `core.hooksPath` points) and
  /// its `core.fsmonitor` command (F45). When it returns false -- and always when it is not given
  /// -- every git this service starts gets [untrustedGitGlobalArgs] first, the same switch-off
  /// the untrusted tier's allowlisted git gets (G-17): the hooks path is an empty directory and
  /// fsmonitor is off. `ControlServer` passes the folder's trust flag, so the person's own
  /// pre-commit hooks still run when they commit from the Git panel in a folder they trust; in a
  /// folder they do not, a checkout or a commit no longer runs whatever the repository brought
  /// with it. `IsolatedTaskRegistry` passes nothing: `git worktree add` for an agent task runs no
  /// `post-checkout` hook, trusted folder or not, since no human started it.
  GitService({
    Duration networkTimeout = const Duration(seconds: 30),
    Duration localTimeout = const Duration(seconds: 45),
    Map<String, String> environment = const {},
    bool Function(String cwd)? runsRepositoryPrograms,
  })  : _networkTimeout = networkTimeout,
        _localTimeout = localTimeout,
        _environment = {...environment, ..._gitEnvironment},
        _runsRepositoryPrograms = runsRepositoryPrograms ?? _never;

  final Duration _networkTimeout;
  final Duration _localTimeout;
  final Map<String, String> _environment;
  final bool Function(String cwd) _runsRepositoryPrograms;

  static bool _never(String cwd) => false;

  /// [args] as git is actually started in [cwd]: behind [untrustedGitGlobalArgs] unless
  /// [_runsRepositoryPrograms] allows the repository's own programs there. A caller that cannot
  /// answer counts as "no".
  List<String> _argv(String cwd, List<String> args) {
    bool allowed;
    try {
      allowed = _runsRepositoryPrograms(cwd);
    } on Object {
      allowed = false;
    }
    return allowed ? args : [...untrustedGitGlobalArgs(sharedEmptyHooksDir()), ...args];
  }

  /// Bounds the cheap read-only probes of [isolationState].
  static const _probeTimeout = Duration(seconds: 10);

  /// WP4 (D-13): whether [cwd] can hold an isolated worktree -- a git work tree with at least one
  /// commit. Two cheap read-only probes, each bounded; a probe that times out throws
  /// [GitCommandError] rather than guessing.
  Future<GitIsolationState> isolationState(String cwd) async {
    final inside = await _runBounded(cwd, ['rev-parse', '--is-inside-work-tree'], _probeTimeout);
    if (inside.exitCode != 0 || inside.stdout.trim() != 'true') return GitIsolationState.notAWorkTree;
    final head = await _runBounded(cwd, ['rev-parse', '--verify', '--quiet', 'HEAD^{commit}'], _probeTimeout);
    return head.exitCode == 0 ? GitIsolationState.ready : GitIsolationState.noCommit;
  }

  /// WP4 (D-13), "Initialize git here": `git init` (skipped when [cwd] already is a work tree
  /// with no commit -- [init] false), `git add -A`, then a first commit with [message], which may
  /// be empty so even an empty folder gets one to branch from. The commit uses the person's own
  /// git identity; only if git has none does it retry once with `AURA <aura@localhost>`, given on
  /// the command line so nothing is written to the repository's config. Returns the commit's sha.
  /// Throws [GitInitError] with git's own message on any failure. The caller checks the folder is
  /// granted and not already [GitIsolationState.ready] (`git_init.dart`).
  Future<String> initRepository(String cwd, {required String message, bool init = true}) async {
    Future<_GitResult> step(List<String> args) async {
      try {
        return await _runBounded(cwd, args, _localTimeout);
      } on GitCommandError catch (error) {
        throw GitInitError('git ${args.first} did not finish', error.message);
      } on GitDecodeError catch (error) {
        throw GitInitError('git ${args.first} failed', error.message);
      } on ProcessException catch (error) {
        throw GitInitError('git could not be started', error.message);
      }
    }

    if (init) {
      final result = await step(['init']);
      if (result.exitCode != 0) throw GitInitError('git init failed', result.stderr.trim());
    }
    final added = await step(['add', '-A']);
    if (added.exitCode != 0) throw GitInitError('git add failed', added.stderr.trim());
    final commitArgs = ['commit', '--allow-empty', '-q', '-m', message];
    var committed = await step(commitArgs);
    if (committed.exitCode != 0 && _missingIdentity(committed.stderr)) {
      committed = await step(['-c', 'user.name=AURA', '-c', 'user.email=aura@localhost', ...commitArgs]);
    }
    if (committed.exitCode != 0) throw GitInitError('git commit failed', committed.stderr.trim());
    final head = await step(['rev-parse', 'HEAD']);
    if (head.exitCode != 0) throw GitInitError('git rev-parse failed', head.stderr.trim());
    return head.stdout.trim();
  }

  /// git's messages for "no user.name/user.email to commit with" (its `ident.c`).
  static bool _missingIdentity(String stderr) =>
      stderr.contains('Please tell me who you are') ||
      stderr.contains('empty ident name') ||
      stderr.contains('unable to auto-detect email address') ||
      stderr.contains('no email was given and auto-detection is disabled') ||
      stderr.contains('no name was given and auto-detection is disabled');

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

  /// [base] is required for [GitDiffScope.branchRange] (D9's "Agent Review" panel: the diff
  /// between a task's branch and whatever it was carved from) and ignored otherwise. Uses git's
  /// triple-dot notation (`base...HEAD`) rather than `base..HEAD` so the diff reflects only what
  /// the branch itself changed, not anything [base] gained in the meantime.
  Future<Map<String, Object?>> diff(
    String cwd, {
    String? file,
    required GitDiffScope scope,
    String? base,
  }) async {
    if (scope == GitDiffScope.branchRange && base == null) {
      throw ArgumentError('base is required for GitDiffScope.branchRange');
    }
    final result = await _runOk(cwd, [
      'diff',
      if (scope == GitDiffScope.staged) '--cached',
      if (scope == GitDiffScope.branchRange) '$base...HEAD',
      if (file != null) ...['--', file],
    ]);
    final truncated = result.stdout.length > _maxDiffCapChars;
    final text = truncated ? result.stdout.substring(0, _maxDiffCapChars) : result.stdout;
    return {'diff': text, 'truncated': truncated};
  }

  /// D9: a compact per-file change summary for a task's branch against [base] -- the "Agent
  /// Review" panel's card shows this instead of a full diff, which could be large.
  Future<Map<String, Object?>> diffStat(String cwd, String base) async {
    final result = await _runOk(cwd, ['diff', '--stat', '$base...HEAD']);
    return {'stat': result.stdout};
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

  /// Lists local branches plus remote-tracking ones, marking the checked-out
  /// branch -- the human Git panel's branch switcher. Remote entries keep their
  /// full `remotes/<remote>/<name>` form; the panel strips that prefix when the
  /// human picks one, so plain `git checkout <short>` DWIM-creates the local
  /// tracking branch. Uses [_run] rather than [_runOk] so a repo with no
  /// commits yet (where `git branch` reports nothing) returns an empty list
  /// instead of throwing -- mirroring [log]'s own empty-repo handling.
  Future<Map<String, Object?>> branches(String cwd) async {
    final result = await _run(cwd, ['branch', '-a', '-vv']);
    if (result.exitCode != 0 && _looksLikeNotARepo(result.stderr)) {
      throw GitNotARepositoryError('not a git repository');
    }
    if (result.exitCode != 0) {
      throw GitCommandError('git branch failed: ${result.stderr}');
    }
    final branches = <Map<String, Object?>>[];
    String? current;
    for (final line in const LineSplitter().convert(result.stdout)) {
      if (line.trim().isEmpty) continue;
      final parsed = _parseBranchLine(line);
      if (parsed == null) continue;
      branches.add(parsed);
      if (parsed['current'] == true && parsed['name'] is String) {
        current = parsed['name'] as String;
      }
    }
    return {'branches': branches, 'current': current};
  }

  /// Parses one `git branch -a -vv` line such as
  /// `* main  a1b2c3d [origin/main: ahead 1] subject` or
  /// `  remotes/origin/foo  e5f6g7h some subject`. Returns null for lines that
  /// are not branch entries (e.g. a detached-HEAD `* (HEAD detached ...)` line,
  /// which carries no switchable name).
  Map<String, Object?>? _parseBranchLine(String line) {
    final match = RegExp(r'^(\*)?\s*(\S+)\s+([0-9a-f]+)\s*(\[([^\]]+)\])?').firstMatch(line);
    if (match == null) return null;
    final name = match.group(2)!;
    if (name.startsWith('(')) return null;
    final tracking = match.group(5);
    String? upstream;
    if (tracking != null) {
      final colon = tracking.indexOf(':');
      upstream = colon >= 0 ? tracking.substring(0, colon) : tracking;
      if (upstream == 'gone') upstream = null;
    }
    return {
      'name': name,
      'current': match.group(1) != null,
      'sha': match.group(3)!,
      'upstream': upstream,
      'is_remote': name.startsWith('remotes/'),
    };
  }

  /// Fetches from every configured remote with pruning -- read-only with
  /// respect to the working tree (only remote-tracking branches move). A repo
  /// with no remotes is a clean no-op rather than an error, so the panel's
  /// Fetch button never fails on a purely local project.
  Future<Map<String, Object?>> fetch(String cwd) async {
    final remotesResult = await _run(cwd, ['remote']);
    if (remotesResult.exitCode != 0) {
      if (_looksLikeNotARepo(remotesResult.stderr)) throw GitNotARepositoryError('not a git repository');
      throw GitCommandError('git remote failed: ${remotesResult.stderr}');
    }
    final remotes = const LineSplitter()
        .convert(remotesResult.stdout)
        .map((line) => line.trim())
        .where((line) => line.isNotEmpty)
        .toList();
    if (remotes.isEmpty) return {'ok': true, 'remotes': const <String>[]};
    final result = await _runNetwork(cwd, ['fetch', '--prune', ...remotes]);
    if (result.exitCode != 0) {
      if (_looksLikeNotARepo(result.stderr)) throw GitNotARepositoryError('not a git repository');
      throw GitCommandError('git fetch failed: ${result.stderr}');
    }
    return {'ok': true, 'remotes': remotes};
  }

  /// Fast-forward-only pull -- deliberately never creates a merge commit or
  /// rebases, so the human is never surprised by history rewriting. Divergent,
  /// unpushed-overwritable, and no-upstream states come back as structured
  /// `{ok: false, reason: ...}` results the panel renders, not exceptions.
  Future<Map<String, Object?>> pull(String cwd) async {
    final result = await _runNetwork(cwd, ['pull', '--ff-only']);
    if (result.exitCode != 0) {
      if (_looksLikeNotARepo(result.stderr)) throw GitNotARepositoryError('not a git repository');
      if (result.stderr.contains('would be overwritten by')) {
        return {
          'ok': false,
          'reason': 'local_changes_would_be_overwritten',
          'affected_files': _parseAffectedFiles(result.stderr),
        };
      }
      if (result.stderr.contains('no tracking information') || result.stderr.contains('no upstream')) {
        return {'ok': false, 'reason': 'no_upstream'};
      }
      if (result.stderr.contains('Not possible to fast-forward')) {
        return {'ok': false, 'reason': 'diverged'};
      }
      throw GitCommandError('git pull failed: ${result.stderr}');
    }
    return {'ok': true};
  }

  /// Pushes the current branch to its upstream with no refspec arguments --
  /// there is deliberately no way to pass a refspec through this method, so a
  /// caller can never redirect the push at another branch (the same class of
  /// guard the server-side `git_push` tool enforces on its own path). Like
  /// [pull], remote-moved and no-upstream states are structured results.
  Future<Map<String, Object?>> push(String cwd) async {
    final result = await _runNetwork(cwd, ['push']);
    if (result.exitCode != 0) {
      if (_looksLikeNotARepo(result.stderr)) throw GitNotARepositoryError('not a git repository');
      if (result.stderr.contains('has no upstream branch') ||
          result.stderr.contains('no upstream') ||
          result.stderr.contains('No configured push destination')) {
        return {'ok': false, 'reason': 'no_upstream'};
      }
      if (result.stderr.contains('non-fast-forward') || result.stderr.contains('fetch first')) {
        return {'ok': false, 'reason': 'diverged'};
      }
      throw GitCommandError('git push failed: ${result.stderr}');
    }
    return {'ok': true};
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
    // Reported in canonical form -- the same one git itself records and prints, and the one
    // containment compares against -- whatever spelling [cwd] arrived in (on Windows, an 8.3
    // short path such as C:\Users\RUNNER~1\... names the same directory as its long form).
    return {'worktree_path': Directory(worktreePath).resolveSymbolicLinksSync(), 'branch': branch};
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

  /// D9: merges [branch] (a task's isolated branch) into whatever is currently checked out in
  /// [cwd] -- always the human's own main worktree, never the task's own worktree, and never a
  /// call the agent itself can trigger (see `control_server.dart`'s `/git/merge-task` route doc
  /// comment for why this is unrelated to `git_push`'s own default-branch guard). `--squash`
  /// never auto-commits by git's own design, so this method commits it itself when requested,
  /// keeping both merge styles a single, complete action from the human's one click.
  Future<Map<String, Object?>> mergeTask(String cwd, String branch, {bool squash = false}) async {
    final result = await _run(cwd, ['merge', squash ? '--squash' : '--no-ff', branch]);
    if (result.exitCode != 0) {
      if (_looksLikeNotARepo(result.stderr)) throw GitNotARepositoryError('not a git repository');
      final conflicted = await _run(cwd, ['diff', '--name-only', '--diff-filter=U']);
      final conflictedFiles =
          const LineSplitter().convert(conflicted.stdout).where((line) => line.trim().isNotEmpty).toList();
      if (conflictedFiles.isNotEmpty) {
        return {'ok': false, 'reason': 'conflict', 'conflicted_files': conflictedFiles};
      }
      throw GitCommandError('git merge failed: ${result.stderr}');
    }
    if (!squash) {
      return {'ok': true, 'committed': true};
    }
    final commitResult = await _run(cwd, ['commit', '-m', 'Merge task branch $branch (squashed)']);
    if (commitResult.exitCode != 0) {
      if (commitResult.stdout.contains('nothing to commit') || commitResult.stderr.contains('nothing to commit')) {
        return {'ok': true, 'committed': false};
      }
      throw GitCommandError('git commit (squash merge) failed: ${commitResult.stderr}');
    }
    return {'ok': true, 'committed': true};
  }

  /// D5 Git panel: merges [branch] into whatever is currently checked out in [cwd] -- the human's
  /// own working tree, not a task worktree (contrast [mergeTask], the agent-only equivalent gated
  /// to `.aura-workspaces/<taskId>` checkouts). Conflict detection mirrors [mergeTask]'s own
  /// technique exactly (`diff --name-only --diff-filter=U` after a failed merge). Unlike
  /// [mergeTask], never auto-commits on success -- an ordinary (non-squash) merge that completes
  /// clean already leaves a merge commit from `git merge` itself; this method only reports it.
  Future<Map<String, Object?>> mergeBranch(String cwd, String branch) async {
    final result = await _run(cwd, ['merge', '--no-ff', branch]);
    if (result.exitCode != 0) {
      if (_looksLikeNotARepo(result.stderr)) throw GitNotARepositoryError('not a git repository');
      final conflicted = await _run(cwd, ['diff', '--name-only', '--diff-filter=U']);
      final conflictedFiles =
          const LineSplitter().convert(conflicted.stdout).where((line) => line.trim().isNotEmpty).toList();
      if (conflictedFiles.isNotEmpty) {
        return {'ok': false, 'reason': 'conflict', 'conflicted_files': conflictedFiles};
      }
      if (result.stderr.contains('Already up to date')) {
        return {'ok': true, 'already_up_to_date': true};
      }
      throw GitCommandError('git merge failed: ${result.stderr}');
    }
    return {'ok': true};
  }

  /// Bails out of a merge left in conflict by [mergeBranch], restoring the working tree to its
  /// pre-merge state (`git merge --abort` is only valid while `MERGE_HEAD` exists, i.e. mid-merge
  /// -- git itself refuses otherwise, surfaced here as a normal [GitCommandError]).
  Future<Map<String, Object?>> abortMerge(String cwd) async {
    final result = await _run(cwd, ['merge', '--abort']);
    if (result.exitCode != 0) {
      if (_looksLikeNotARepo(result.stderr)) throw GitNotARepositoryError('not a git repository');
      throw GitCommandError('git merge --abort failed: ${result.stderr}');
    }
    return {'ok': true};
  }

  bool _looksLikeNotARepo(String stderr) => stderr.contains('not a git repository');

  List<String> _parseAffectedFiles(String stderr) {
    final files = <String>[];
    var collecting = false;
    for (final line in const LineSplitter().convert(stderr)) {
      // Matches both checkout's "would be overwritten by checkout" and merge/pull's
      // "would be overwritten by merge" -- the affected-file list format is identical.
      if (line.contains('would be overwritten by')) {
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
    final result = await Process.run(
      'git',
      _argv(cwd, args),
      workingDirectory: cwd,
      environment: _environment,
      stdoutEncoding: null,
      stderrEncoding: null,
    );
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

  /// [fetch]/[pull]/[push] talk to a remote, so unlike every other command in this file they can
  /// block on something outside this machine: a slow/unresponsive server, or -- absent
  /// [_gitEnvironment]'s `GIT_TERMINAL_PROMPT=0` -- git itself blocking on a username/password
  /// prompt this headless daemon has no way to answer. [_run]/[Process.run] has no timeout of its
  /// own and doesn't expose the spawned [Process], so a hung call there can never be cancelled;
  /// this uses [Process.start] instead specifically so a timeout can actually [Process.kill] it,
  /// rather than merely giving up on waiting for it while it keeps running (and keeps the repo's
  /// `.git/index.lock`, if any) in the background.
  Future<_GitResult> _runNetwork(String cwd, List<String> args) => _runBounded(cwd, args, _networkTimeout);

  /// [args] run with a [timeout] after which git (and, on Windows, its child) is killed and
  /// [GitCommandError] thrown -- [_runNetwork]'s mechanism, also used for WP4's local steps.
  Future<_GitResult> _runBounded(String cwd, List<String> args, Duration timeout) async {
    final process =
        await Process.start('git', _argv(cwd, args), workingDirectory: cwd, environment: _environment);
    final stdoutBytes = process.stdout.fold<List<int>>(<int>[], (acc, chunk) => acc..addAll(chunk));
    final stderrBytes = process.stderr.fold<List<int>>(<int>[], (acc, chunk) => acc..addAll(chunk));
    int exitCode;
    try {
      exitCode = await process.exitCode.timeout(timeout);
    } on TimeoutException {
      await _killTree(process);
      throw GitCommandError('git ${args.join(' ')} timed out after ${timeout.inSeconds}s');
    }
    try {
      return _GitResult(exitCode: exitCode, stdout: utf8.decode(await stdoutBytes), stderr: utf8.decode(await stderrBytes));
    } on FormatException {
      throw GitDecodeError('git output is not valid UTF-8 text');
    }
  }

  /// Kills a timed-out network git and waits until it is actually gone, so nothing it holds (the
  /// repo as its working directory, `.git/index.lock`, the transport socket) outlives the error.
  ///
  /// On Windows the `git.exe` on PATH is normally Git for Windows' launcher (`cmd\git.exe` or
  /// `bin\git.exe`), which runs the real `mingw64\bin\git.exe` as a child. [Process.kill] is a
  /// TerminateProcess of the launcher alone, so the real git kept running with the repo as its
  /// working directory -- on the Windows runner the test's cleanup then failed to delete it
  /// (errno 32, in use by another process). `taskkill /T` takes the whole tree, and has to run
  /// while the launcher is still alive, since that is how it finds the children.
  Future<void> _killTree(Process process) async {
    if (Platform.isWindows) {
      try {
        await Process.run('taskkill', ['/F', '/T', '/PID', '${process.pid}'])
            .timeout(const Duration(seconds: 10));
      } on Object {
        // Fall through to the plain kill below; it at least ends the launcher.
      }
    }
    process.kill(ProcessSignal.sigkill);
    await process.exitCode.timeout(const Duration(seconds: 10), onTimeout: () => -1);
  }
}

/// Applied to every git invocation in this file: makes git fail fast with a normal non-zero exit
/// (already handled by every caller here) instead of blocking on an interactive username/password
/// prompt that this headless daemon process has no way to answer. Without it, a fetch/pull/push
/// against a remote with no preconfigured credential helper can hang indefinitely from the human's
/// point of view -- the Git panel's button just spins forever.
const _gitEnvironment = {'GIT_TERMINAL_PROMPT': '0'};

class _StatusHeader {
  const _StatusHeader({required this.branch, required this.upstream, required this.ahead, required this.behind});
  final String? branch;
  final String? upstream;
  final int ahead;
  final int behind;
}
