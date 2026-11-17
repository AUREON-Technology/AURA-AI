/// D8/D9: shared bookkeeping for per-task isolated git worktrees (D8 plan Part B).
///
/// Owned by exactly one instance per daemon run (constructed in `bin/aura_hostd.dart`) and handed
/// to both [TaskHandler] (agent-facing: resolves an isolated task's writes/runs against its own
/// worktree) and `ControlServer` (D9's human-facing "Agent Review" panel: lists/reviews/merges/
/// discards them) -- without a shared instance, `ControlServer` would have no way to see what
/// `TaskHandler` has isolated on the agent's behalf. Extracted out of `task_handler.dart` (D8)
/// specifically so Part C could be added without `ControlServer` reaching into `TaskHandler`'s
/// private state, or the two daemon surfaces needing a reference to each other.
library;

import 'dart:io';

import 'package:path/path.dart' as p;

import 'containment.dart';
import 'git_service.dart';

/// Recorded once per task_id at `TaskHandler.createTask` time, so a task's later write/run calls
/// (which only ever carry `task_id`, not the original `isolate`/`task_key` params) can still find
/// their way to the right worktree.
class TaskIsolationConfig {
  const TaskIsolationConfig({required this.taskKey, this.baseBranch});
  final String taskKey;
  final String? baseBranch;
}

/// One isolated task's worktree, keyed by task_key. Carries [parentRootPath]/[branch] alongside
/// [root] so [IsolatedTaskRegistry.discard] (D9) can tear both down without re-deriving them, and
/// [taskKey] itself so a listing (D9's "Agent Review" panel) doesn't need the map key separately.
class IsolatedTask {
  const IsolatedTask({
    required this.taskKey,
    required this.root,
    required this.parentRootPath,
    required this.branch,
  });

  final String taskKey;
  final WorkspaceRoot root;
  final String parentRootPath;
  final String branch;

  Map<String, Object?> toJson() => {
        'task_key': taskKey,
        'worktree_path': root.canonicalPath,
        'parent_root': parentRootPath,
        'branch': branch,
      };
}

class TaskNotFoundError implements Exception {
  TaskNotFoundError(this.taskKey);
  final String taskKey;
  @override
  String toString() => 'no isolated workspace is known for task_key "$taskKey"';
}

class IsolatedTaskRegistry {
  IsolatedTaskRegistry(this._roots, [GitService? gitService]) : _git = gitService ?? GitService();

  final WorkspaceRoots _roots;

  /// A dedicated `GitService` instance, never shared with `ControlServer`'s own (the deliberate
  /// human-only bypass, `git_service.dart`'s own doc comment) or `TaskHandler`'s -- this one only
  /// ever runs `worktree add`/`remove`/`branch -D` on the agent's behalf, still reached only
  /// through this registry's own containment-respecting resolution logic below.
  final GitService _git;

  final Map<String, TaskIsolationConfig> _taskConfigs = {};
  final Map<String, WorkspaceRoot> _taskRoots = {};
  final Map<String, IsolatedTask> _taskKeyWorktrees = {};

  /// Records that [taskId] wants isolation under [taskKey], and binds it immediately if
  /// [taskKey] already has a worktree (a later turn in the same chat session).
  void registerConfig(String taskId, {required String taskKey, String? baseBranch}) {
    _taskConfigs[taskId] = TaskIsolationConfig(taskKey: taskKey, baseBranch: baseBranch);
    final existing = _taskKeyWorktrees[taskKey];
    if (existing != null) _taskRoots[taskId] = existing.root;
  }

  /// Synchronous fast path -- see [needsResolution]'s doc comment for why callers must check
  /// this (and that) before ever awaiting [resolve], not call it unconditionally.
  ///
  /// The returned root is re-derived from the *current* grants (audit INT-002): if the parent
  /// root the worktree was carved out of has since been revoked this throws [RootRevokedError],
  /// and the worktree's trust always follows the parent's trust as it is now, not as it was when
  /// the worktree was created -- so untrusting a project immediately restores the approval gate
  /// for its isolated tasks too.
  WorkspaceRoot? boundRoot(String? taskId) {
    if (taskId == null) return null;
    final bound = _taskRoots[taskId];
    if (bound == null) return null;
    return _live(bound, _taskConfigs[taskId]?.taskKey);
  }

  WorkspaceRoot _live(WorkspaceRoot bound, String? taskKey) {
    final info = taskKey == null ? null : _taskKeyWorktrees[taskKey];
    // Discarded (or never recorded): nothing to re-derive the parent from. The worktree itself
    // is gone with it, so any filesystem call against it fails on its own.
    if (info == null) return bound;
    final parent = _roots.byCanonicalPath(info.parentRootPath);
    if (parent == null) {
      throw RootRevokedError(
        'workspace root ${info.parentRootPath} was revoked -- its isolated task workspace can no '
        'longer be used; grant the folder again to continue',
      );
    }
    return parent.trusted == bound.trusted ? bound : bound.copyWith(trusted: parent.trusted);
  }

  /// `true` only when [taskId] is genuinely configured for isolation and not yet bound to a
  /// root -- i.e. the only case where [resolve] might need to do real async work (checking a
  /// directory, possibly running `git worktree add`). Even a no-op `await` on an `async` method
  /// costs one microtask hop in Dart, which is enough to break call sites elsewhere in this
  /// codebase that check state synchronously right after firing off a call without awaiting it
  /// (`task_handler.dart`'s write-gating tests) -- callers must gate on this before awaiting
  /// [resolve], never await it unconditionally.
  bool needsResolution(String? taskId) =>
      taskId != null && _taskConfigs.containsKey(taskId) && _taskRoots[taskId] == null;

  /// Resolves the effective [WorkspaceRoot] a [taskId]'s call should use: an already-bound
  /// ephemeral root, a newly-discovered one (this task's first isolated call), or `null` to fall
  /// back to the caller's own ordinary cross-all-roots search. [pathForRootLookup] is whatever
  /// path/cwd the actual RPC named -- used only to find *which* granted root the worktree should
  /// be carved out of; once found, every subsequent call for this task_id (any path) resolves
  /// against the worktree instead, never the search result again (D8 plan Part B.2.3).
  Future<WorkspaceRoot?> resolve(String? taskId, String? pathForRootLookup) async {
    if (taskId == null) return null;
    final bound = _taskRoots[taskId];
    if (bound != null) return _live(bound, _taskConfigs[taskId]?.taskKey);

    final config = _taskConfigs[taskId];
    if (config == null) return null;
    final taskKey = config.taskKey;

    final existing = _taskKeyWorktrees[taskKey];
    if (existing != null) {
      _taskRoots[taskId] = existing.root;
      return _live(existing.root, taskKey);
    }

    final parentRoot = pathForRootLookup != null
        ? _roots.findContainingRoot(pathForRootLookup)
        : (_roots.all.isNotEmpty ? _roots.all.first : null);
    if (parentRoot == null) return null;

    final worktreePath = p.join(parentRoot.canonicalPath, '.aura-workspaces', taskKey);
    final branch = 'aura/task-$taskKey';
    String canonicalWorktreePath;
    if (Directory(worktreePath).existsSync()) {
      // Recovered across a daemon restart: the worktree/branch already exist on disk from an
      // earlier run (the in-memory maps above don't survive a restart, but the worktree itself
      // does) -- re-register rather than trying, and failing, to recreate it. This is what makes
      // this daemon's own restart-durability story work without a separate `reopen_task` RPC or
      // backend-side store: the filesystem is the durable source of truth, these maps are just a
      // warm cache of it.
      canonicalWorktreePath = Directory(worktreePath).resolveSymbolicLinksSync();
    } else {
      final created = await _git.createWorktree(
        parentRoot.canonicalPath,
        taskId: taskKey,
        baseBranch: config.baseBranch,
      );
      canonicalWorktreePath = File(created['worktree_path'] as String).resolveSymbolicLinksSync();
    }

    // Inherits the parent root's trust exactly -- no widening, no narrowing (D8 plan, locked
    // decision: an isolated task must never be more permissive than the project it's isolated
    // from, and an already-trusted project should see no new friction).
    final ephemeralRoot = WorkspaceRoot(canonicalPath: canonicalWorktreePath, trusted: parentRoot.trusted);
    _taskKeyWorktrees[taskKey] = IsolatedTask(
      taskKey: taskKey,
      root: ephemeralRoot,
      parentRootPath: parentRoot.canonicalPath,
      branch: branch,
    );
    _taskRoots[taskId] = ephemeralRoot;
    return ephemeralRoot;
  }

  /// D9's "Agent Review" panel data source -- every isolated task this daemon currently knows
  /// about, whether from this run or recovered from disk on first touch after a restart.
  List<IsolatedTask> list() => _taskKeyWorktrees.values.toList(growable: false);

  IsolatedTask? infoFor(String taskKey) => _taskKeyWorktrees[taskKey];

  /// Tears down the isolated worktree/branch for [taskKey] entirely. Only ever invoked by a
  /// human's explicit "Discard" action (D9, `ControlServer`'s `/git/discard-task` route), never
  /// by the agent itself. Throws [TaskNotFoundError] for an unknown task_key.
  Future<Map<String, Object?>> discard(String taskKey) async {
    final info = _taskKeyWorktrees.remove(taskKey);
    if (info == null) {
      throw TaskNotFoundError(taskKey);
    }
    return _git.removeWorktree(info.parentRootPath, info.root.canonicalPath, branch: info.branch);
  }
}
