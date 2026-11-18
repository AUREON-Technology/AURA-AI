/// Git state notifier (D5) -- source of truth for the Git panel
/// (`aura_client/lib/desktop/git/git_panel.dart`).
///
/// Unlike [TerminalNotifier] (a live WS stream) or [HostNotifier] (a 3s poll of daemon
/// reachability), this notifier refreshes only on explicit triggers -- workspace open, after a
/// write action, or a manual refresh click (D5 plan design decision 2) -- since git state is not
/// something that needs continuous watching. It also does not self-subscribe to `WorkspaceOpened`
/// on the event bus the way [TerminalNotifier] does: that event carries only a `workspaceId`, not
/// the daemon-side `rootPath` this notifier actually needs, so the Git panel widget calls
/// [refresh] itself once it knows both -- mirroring `terminal_view.dart`'s `_maybeAutoStart`
/// pattern rather than `TerminalNotifier`'s DB-backed one.
library;

import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter_riverpod/legacy.dart';

class GitNotifier extends StateNotifier<GitState> {
  GitNotifier(this._eventBus, this._hostdClient) : super(const GitState());

  final IdeEventBus _eventBus;
  final HostdControlClient _hostdClient;

  String? _rootPath;
  String? _lastWorkspaceId;

  /// Fetches `GET /git/status` for [rootPath] and replaces state wholesale (including clearing
  /// any previously loaded diff -- a workspace switch or manual refresh is a real context change,
  /// not an incremental update). A `409 not a git repository` or an unreachable daemon both land
  /// in [GitState.error] rather than throwing, so the panel can show a clear message instead of
  /// crashing.
  Future<void> refresh(String workspaceId, String rootPath) async {
    _rootPath = rootPath;
    _lastWorkspaceId = workspaceId;
    try {
      final result = await _hostdClient.gitStatus(root: rootPath);
      final branches = await _loadBranchesQuietly(rootPath);
      final files = _filesFrom(result);
      state = GitState(
        workspaceId: workspaceId,
        branch: result['branch'] as String?,
        upstream: result['upstream'] as String?,
        ahead: result['ahead'] as int? ?? 0,
        behind: result['behind'] as int? ?? 0,
        files: files,
        branches: branches,
        conflictedFiles: _unmergedPathsFrom(files),
      );
      _publishChanged();
    } on HostdControlError catch (error) {
      state = GitState(workspaceId: workspaceId, error: error.message);
    } on HostdControlUnavailable catch (error) {
      state = GitState(workspaceId: workspaceId, error: error.message);
    }
  }

  /// Branch listing is best-effort inside [refresh]: a status that loaded must
  /// not be discarded just because the branch list failed, so failures here
  /// degrade to an empty list the panel renders as "current branch only".
  Future<List<GitBranch>> _loadBranchesQuietly(String rootPath) async {
    try {
      final result = await _hostdClient.gitBranches(root: rootPath);
      return ((result['branches'] as List?) ?? const [])
          .cast<Map<String, dynamic>>()
          .map(GitBranch.fromJson)
          .toList();
    } on HostdControlError {
      return const [];
    } on HostdControlUnavailable {
      return const [];
    }
  }

  Future<void> loadBranches() async {
    final root = _rootPath;
    if (root == null) return;
    try {
      final result = await _hostdClient.gitBranches(root: root);
      final branches = ((result['branches'] as List?) ?? const [])
          .cast<Map<String, dynamic>>()
          .map(GitBranch.fromJson)
          .toList();
      state = state.copyWith(branches: branches, clearError: true);
    } on HostdControlError catch (error) {
      state = state.copyWith(error: error.message);
    } on HostdControlUnavailable catch (error) {
      state = state.copyWith(error: error.message);
    }
  }

  Future<void> loadDiff({String? file, required GitDiffScope scope}) async {
    final root = _rootPath;
    if (root == null) return;
    try {
      final result = await _hostdClient.gitDiff(root: root, file: file, staged: scope == GitDiffScope.staged);
      state = state.copyWith(
        diffText: result['diff'] as String? ?? '',
        diffScope: scope,
        diffTruncated: result['truncated'] as bool? ?? false,
        clearError: true,
      );
    } on HostdControlError catch (error) {
      state = state.copyWith(error: error.message);
    } on HostdControlUnavailable catch (error) {
      state = state.copyWith(error: error.message);
    }
  }

  Future<void> loadLog({int limit = 30}) async {
    final root = _rootPath;
    if (root == null) return;
    try {
      final result = await _hostdClient.gitLog(root: root, limit: limit);
      final commits = ((result['commits'] as List?) ?? const [])
          .cast<Map<String, dynamic>>()
          .map(GitCommit.fromJson)
          .toList();
      state = state.copyWith(commits: commits, clearError: true);
    } on HostdControlError catch (error) {
      state = state.copyWith(error: error.message);
    } on HostdControlUnavailable catch (error) {
      state = state.copyWith(error: error.message);
    }
  }

  Future<void> stage(List<String> paths) => _write(() => _hostdClient.gitStage(root: _rootPath!, paths: paths));

  /// Stages every currently-unstaged file -- computed client-side from [GitState.files] rather
  /// than sending an `all: true` flag, so the "stage all" button always acts on exactly what the
  /// panel is showing.
  Future<void> stageAll() => stage(state.files.where((f) => !f.staged).map((f) => f.path).toList());

  Future<void> unstage(List<String> paths) =>
      _write(() => _hostdClient.gitUnstage(root: _rootPath!, paths: paths));

  Future<void> commit(String message) => _write(() async {
        final result = await _hostdClient.gitCommit(root: _rootPath!, message: message);
        if (result['ok'] == false) {
          state = state.copyWith(error: currentL10n.gitNothingStaged);
        }
      });

  Future<void> checkout(String branch, {bool create = false}) => _write(() async {
        final result = await _hostdClient.gitCheckout(root: _rootPath!, branch: branch, create: create);
        if (result['ok'] == false) {
          final affected = ((result['affected_files'] as List?) ?? const []).join(', ');
          state = state.copyWith(
            error: affected.isEmpty
                ? currentL10n.gitCheckoutOverwrite
                : currentL10n.gitCheckoutOverwriteIn(affected),
          );
        }
      });

  /// D5 Git panel: merges [branch] into the human's own checked-out working tree. Deliberately
  /// does not set [GitState.error]/[GitState.conflictedFiles] itself on a conflict result --
  /// [_write] always calls [refresh] right after `op()` returns (success or a structured
  /// `ok: false`, not just on a thrown error), and [refresh] derives [GitState.conflictedFiles]
  /// straight from real `git status` unmerged-path codes, which is the authoritative source right
  /// after a conflicted merge. Setting it here too would just be overwritten by that refresh a
  /// moment later. The panel's resolution UI keys off `conflictedFiles.isNotEmpty`, not a
  /// transient error string.
  Future<void> mergeBranch(String branch) => _write(() => _hostdClient.gitMerge(root: _rootPath!, branch: branch));

  /// Bails out of a merge [mergeBranch] left in conflict, restoring the working tree. The
  /// subsequent [_write]-triggered [refresh] naturally reports an empty [GitState.conflictedFiles]
  /// once `git status` no longer shows unmerged paths.
  Future<void> abortMerge() => _write(() => _hostdClient.gitMergeAbort(root: _rootPath!));

  /// Human-only remote sync -- fetch is read-only w.r.t. the working tree,
  /// pull is fast-forward-only, push sends the checked-out branch to its own
  /// upstream (the daemon takes no refspec). All three refresh status on
  /// success via [_write]; structured daemon failures become panel errors.
  Future<void> fetch() => _write(() => _hostdClient.gitFetch(root: _rootPath!));

  Future<void> pull() => _write(() async {
        final result = await _hostdClient.gitPull(root: _rootPath!);
        if (result['ok'] == false) _applySyncFailure(result);
      });

  Future<void> push() => _write(() async {
        final result = await _hostdClient.gitPush(root: _rootPath!);
        if (result['ok'] == false) _applySyncFailure(result);
      });

  /// Maps the daemon's snake_case `reason` code to the panel's message. No BuildContext here, so
  /// [currentL10n]: the error is shown as soon as it is set.
  void _applySyncFailure(Map<String, dynamic> result) {
    final l10n = currentL10n;
    final affected = ((result['affected_files'] as List?) ?? const []).join(', ');
    state = state.copyWith(
      error: switch (result['reason']) {
        'no_upstream' => l10n.gitNoUpstream,
        'diverged' => l10n.gitDiverged,
        'local_changes_would_be_overwritten' => affected.isEmpty
            ? l10n.gitLocalChangesOverwritten
            : l10n.gitLocalChangesOverwrittenIn(affected),
        _ => l10n.gitRemoteSyncFailed,
      },
    );
  }

  /// Runs [op] under the [GitState.busy] guard: a no-op while already busy (prevents a
  /// double-click firing two concurrent writes -- D5 plan design decision 10), then refreshes
  /// status afterward on success so the panel reflects the new state.
  Future<void> _write(Future<void> Function() op) async {
    if (state.busy || _rootPath == null || _lastWorkspaceId == null) return;
    state = state.copyWith(busy: true, clearError: true);
    try {
      await op();
      await refresh(_lastWorkspaceId!, _rootPath!);
    } on HostdControlError catch (error) {
      state = state.copyWith(busy: false, error: error.message);
      return;
    } on HostdControlUnavailable catch (error) {
      state = state.copyWith(busy: false, error: error.message);
      return;
    }
    state = state.copyWith(busy: false);
  }

  void _publishChanged() {
    final workspaceId = _lastWorkspaceId;
    if (workspaceId == null) return;
    _eventBus.publish(GitChanged(
      workspaceId,
      branch: state.branch,
      status: '${state.files.length} changed',
    ));
  }

  List<GitFileChange> _filesFrom(Map<String, dynamic> result) => ((result['files'] as List?) ?? const [])
      .cast<Map<String, dynamic>>()
      .map(GitFileChange.fromJson)
      .toList();

  /// `git status --short`'s unmerged-path codes (both `X`/`Y` columns non-space, and matching one
  /// of `UU`/`AA`/`DD`/`AU`/`UD`/`UA`/`DU`) -- deriving [GitState.conflictedFiles] straight from
  /// the real status on every [refresh] rather than trusting a one-time snapshot from whichever
  /// call caused the conflict means it stays correct even after the human manually resolves and
  /// stages a file outside this notifier's own knowledge (an editor save, a terminal `git add`).
  static const _unmergedStatusPairs = {'UU', 'AA', 'DD', 'AU', 'UD', 'UA', 'DU'};

  List<String> _unmergedPathsFrom(List<GitFileChange> files) => files
      .where((f) => _unmergedStatusPairs.contains('${f.indexStatus}${f.worktreeStatus}'))
      .map((f) => f.path)
      .toList();
}
