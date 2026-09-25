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
      state = GitState(
        workspaceId: workspaceId,
        branch: result['branch'] as String?,
        upstream: result['upstream'] as String?,
        ahead: result['ahead'] as int? ?? 0,
        behind: result['behind'] as int? ?? 0,
        files: _filesFrom(result),
        branches: branches,
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
          state = state.copyWith(error: 'Nothing is staged to commit.');
        }
      });

  Future<void> checkout(String branch, {bool create = false}) => _write(() async {
        final result = await _hostdClient.gitCheckout(root: _rootPath!, branch: branch, create: create);
        if (result['ok'] == false) {
          final affected = ((result['affected_files'] as List?) ?? const []).join(', ');
          state = state.copyWith(
            error: affected.isEmpty
                ? 'Switching branches would overwrite local changes.'
                : 'Switching branches would overwrite local changes in: $affected',
          );
        }
      });

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

  void _applySyncFailure(Map<String, dynamic> result) {
    final affected = ((result['affected_files'] as List?) ?? const []).join(', ');
    state = state.copyWith(
      error: switch (result['reason']) {
        'no_upstream' => 'The current branch has no upstream set.',
        'diverged' => 'The branch has diverged -- pull (or push) cannot fast-forward.',
        'local_changes_would_be_overwritten' => affected.isEmpty
            ? 'Local changes would be overwritten.'
            : 'Local changes would be overwritten in: $affected',
        _ => 'Remote sync failed.',
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
}
