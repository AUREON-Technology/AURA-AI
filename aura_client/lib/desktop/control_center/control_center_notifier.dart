/// Control Center state notifier for Desktop IDE's "Agent Review" panel (D9).
///
/// Lists every isolated per-task worktree `aura_hostd` currently knows about
/// (`GET /tasks`, backed by `isolated_task_registry.dart`'s shared registry) and drives the
/// human-only review/merge/discard actions on them (`control_server.dart`'s new D9 routes).
/// Unlike [GitNotifier] this notifier is not scoped to the active workspace -- a task's worktree
/// lives inside whichever project it was carved from, which may not be the workspace currently
/// open in the editor, so the panel lists every task the daemon reports regardless.
library;

import 'dart:async';

import 'package:flutter_riverpod/legacy.dart';

import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/l10n/l10n.dart';

class ControlCenterTask {
  const ControlCenterTask({
    required this.taskKey,
    required this.branch,
    required this.worktreePath,
    required this.parentRoot,
    this.diffStat,
  });

  final String taskKey;
  final String branch;
  final String worktreePath;
  final String parentRoot;

  /// `null` until [ControlCenterNotifier._loadDiffStat] resolves for this task -- the card shows
  /// a loading placeholder in the meantime rather than a blank stat.
  final String? diffStat;

  static ControlCenterTask fromJson(Map<String, dynamic> json) => ControlCenterTask(
        taskKey: json['task_key'] as String,
        branch: json['branch'] as String,
        worktreePath: json['worktree_path'] as String,
        parentRoot: json['parent_root'] as String,
      );

  ControlCenterTask copyWith({String? diffStat}) => ControlCenterTask(
        taskKey: taskKey,
        branch: branch,
        worktreePath: worktreePath,
        parentRoot: parentRoot,
        diffStat: diffStat ?? this.diffStat,
      );
}

class ControlCenterState {
  const ControlCenterState({
    this.tasks = const [],
    this.loading = false,
    this.busyTaskKey,
    this.error,
    this.expandedTaskKey,
    this.diffText,
    this.conflictTaskKey,
    this.conflictedFiles = const [],
  });

  final List<ControlCenterTask> tasks;
  final bool loading;

  /// The task a merge/discard is currently in flight for -- disables that card's own buttons
  /// without blocking interaction with the rest of the list (D9 plan: two independent tasks
  /// should not contend with each other's actions).
  final String? busyTaskKey;
  final String? error;

  /// Which task's inline diff view is currently expanded, if any.
  final String? expandedTaskKey;
  final String? diffText;

  /// Set after a merge attempt comes back with `{'ok': false, 'reason': 'conflict'}` -- surfaced
  /// as a dedicated banner on that task's card rather than a generic error, per the plan's "no
  /// automated conflict resolution, leave it resolvable" requirement.
  final String? conflictTaskKey;
  final List<String> conflictedFiles;

  ControlCenterState copyWith({
    List<ControlCenterTask>? tasks,
    bool? loading,
    String? busyTaskKey,
    bool clearBusy = false,
    String? error,
    bool clearError = false,
    String? expandedTaskKey,
    bool clearExpanded = false,
    String? diffText,
    bool clearDiffText = false,
    String? conflictTaskKey,
    bool clearConflict = false,
    List<String>? conflictedFiles,
  }) {
    return ControlCenterState(
      tasks: tasks ?? this.tasks,
      loading: loading ?? this.loading,
      busyTaskKey: clearBusy ? null : (busyTaskKey ?? this.busyTaskKey),
      error: clearError ? null : (error ?? this.error),
      expandedTaskKey: clearExpanded ? null : (expandedTaskKey ?? this.expandedTaskKey),
      diffText: clearDiffText ? null : (diffText ?? this.diffText),
      conflictTaskKey: clearConflict ? null : (conflictTaskKey ?? this.conflictTaskKey),
      conflictedFiles: clearConflict ? const [] : (conflictedFiles ?? this.conflictedFiles),
    );
  }
}

class ControlCenterNotifier extends StateNotifier<ControlCenterState> {
  ControlCenterNotifier(this._hostdClient) : super(const ControlCenterState());

  final HostdControlClient _hostdClient;

  Future<void> refresh() async {
    state = state.copyWith(loading: true, clearError: true);
    try {
      final raw = await _hostdClient.tasksList();
      final tasks = raw.map(ControlCenterTask.fromJson).toList();
      state = state.copyWith(tasks: tasks, loading: false, clearError: true);
      for (final task in tasks) {
        unawaited(_loadDiffStat(task));
      }
    } on HostdControlError catch (error) {
      state = state.copyWith(loading: false, error: error.message);
    } on HostdControlUnavailable catch (error) {
      state = state.copyWith(loading: false, error: error.message);
    }
  }

  /// The comparison base for a task's diff is the parent project's *current* branch, read fresh
  /// each time rather than stored at task-creation time -- a merge target can move underneath a
  /// long-lived task, and `git_service.dart`'s own triple-dot notation is what makes that the
  /// right comparison regardless (see its doc comment).
  Future<String?> _baseBranchFor(String parentRoot) async {
    try {
      final status = await _hostdClient.gitStatus(root: parentRoot);
      return status['branch'] as String?;
    } on HostdControlError {
      return null;
    } on HostdControlUnavailable {
      return null;
    }
  }

  Future<void> _loadDiffStat(ControlCenterTask task) async {
    final base = await _baseBranchFor(task.parentRoot);
    if (base == null) return;
    try {
      final result = await _hostdClient.gitTaskDiffStat(taskKey: task.taskKey, base: base);
      _replaceTask(task.taskKey, (t) => t.copyWith(diffStat: (result['stat'] as String?) ?? currentL10n.controlCenterNoChanges));
    } on HostdControlError catch (error) {
      _replaceTask(task.taskKey, (t) => t.copyWith(diffStat: currentL10n.controlCenterDiffStatError(error.message)));
    } on HostdControlUnavailable {
      // Daemon dropped between refresh() and this call -- leave diffStat null, refresh will retry.
    }
  }

  void _replaceTask(String taskKey, ControlCenterTask Function(ControlCenterTask) update) {
    state = state.copyWith(tasks: [for (final t in state.tasks) if (t.taskKey == taskKey) update(t) else t]);
  }

  /// Expands or collapses [task]'s inline branch-range diff -- mirrors the Git panel's own
  /// click-to-toggle pattern rather than a separate page, since a task card is already the
  /// smallest unit of review here.
  Future<void> toggleDiff(ControlCenterTask task) async {
    if (state.expandedTaskKey == task.taskKey) {
      state = state.copyWith(clearExpanded: true, clearDiffText: true);
      return;
    }
    state = state.copyWith(expandedTaskKey: task.taskKey, clearDiffText: true, clearError: true);
    final base = await _baseBranchFor(task.parentRoot);
    if (base == null) {
      state = state.copyWith(error: currentL10n.controlCenterBaseBranchUnknown);
      return;
    }
    try {
      final result = await _hostdClient.gitTaskDiff(taskKey: task.taskKey, base: base);
      if (state.expandedTaskKey == task.taskKey) {
        state = state.copyWith(diffText: (result['diff'] as String?) ?? '');
      }
    } on HostdControlError catch (error) {
      state = state.copyWith(error: error.message);
    } on HostdControlUnavailable catch (error) {
      state = state.copyWith(error: error.message);
    }
  }

  /// Merges [task]'s branch into whatever the human currently has checked out in the parent
  /// project -- local-only, human-initiated (see `control_server.dart::_handleGitMergeTask`'s own
  /// doc comment on why this never touches `git_push`'s guard). Returns `true` on a clean merge so
  /// the panel can prompt about discarding the now-merged worktree; a conflict is surfaced via
  /// [ControlCenterState.conflictTaskKey] instead of being treated as a thrown error.
  Future<bool> merge(ControlCenterTask task, {bool squash = false}) async {
    state = state.copyWith(busyTaskKey: task.taskKey, clearError: true, clearConflict: true);
    try {
      final result = await _hostdClient.gitMergeTask(root: task.parentRoot, taskBranch: task.branch, squash: squash);
      if (result['ok'] == false) {
        state = state.copyWith(
          clearBusy: true,
          conflictTaskKey: task.taskKey,
          conflictedFiles: ((result['conflicted_files'] as List?) ?? const []).cast<String>(),
        );
        return false;
      }
      state = state.copyWith(clearBusy: true);
      return true;
    } on HostdControlError catch (error) {
      state = state.copyWith(clearBusy: true, error: error.message);
      return false;
    } on HostdControlUnavailable catch (error) {
      state = state.copyWith(clearBusy: true, error: error.message);
      return false;
    }
  }

  /// Tears down [taskKey]'s worktree and branch entirely. The client-side confirmation dialog is
  /// the panel's responsibility (`control_center_panel.dart`) -- this method performs the
  /// destructive action unconditionally once called, exactly like every other write method in
  /// this codebase's notifiers.
  Future<void> discard(String taskKey) async {
    state = state.copyWith(busyTaskKey: taskKey, clearError: true);
    try {
      await _hostdClient.gitDiscardTask(taskKey: taskKey);
      state = state.copyWith(
        clearBusy: true,
        tasks: state.tasks.where((t) => t.taskKey != taskKey).toList(),
        clearExpanded: state.expandedTaskKey == taskKey,
        clearConflict: state.conflictTaskKey == taskKey,
      );
    } on HostdControlError catch (error) {
      state = state.copyWith(clearBusy: true, error: error.message);
    } on HostdControlUnavailable catch (error) {
      state = state.copyWith(clearBusy: true, error: error.message);
    }
  }
}

final controlCenterNotifierProvider = StateNotifierProvider<ControlCenterNotifier, ControlCenterState>((ref) {
  return ControlCenterNotifier(ref.watch(hostdControlClientProvider));
});
