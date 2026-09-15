/// Persists granted workspace roots to a local JSON file so a restart of the daemon does not
/// silently forget what the user has already granted (and, just as important, does not silently
/// re-grant something removed).
library;

import 'dart:convert';
import 'dart:io';

import 'containment.dart';

class WorkspaceStore {
  WorkspaceStore(this._file);

  factory WorkspaceStore.forConfigDir(Directory configDir) =>
      WorkspaceStore(File('${configDir.path}/workspaces.json'));

  final File _file;

  WorkspaceRoots load() {
    if (!_file.existsSync()) {
      return WorkspaceRoots(const []);
    }
    final raw = _file.readAsStringSync();
    if (raw.trim().isEmpty) {
      return WorkspaceRoots(const []);
    }
    final decoded = jsonDecode(raw);
    if (decoded is! List) {
      return WorkspaceRoots(const []);
    }
    final roots = decoded
        .whereType<Map<String, dynamic>>()
        .map(WorkspaceRoot.fromJson)
        .toList(growable: false);
    return WorkspaceRoots(roots);
  }

  void save(WorkspaceRoots roots) {
    _file.parent.createSync(recursive: true);
    final json = jsonEncode(roots.all.map((r) => r.toJson()).toList());
    _file.writeAsStringSync(json);
  }
}

/// One durable record of a D8 per-task git worktree (`task_handler.dart`'s `createTask`
/// isolation path, `docs/architecture/015-desktop-ide.md` Part B): which chat session it belongs
/// to, which granted root it was carved out of, and where its own working directory and branch
/// live.
class TaskWorkspace {
  TaskWorkspace({
    required this.taskKey,
    required this.taskId,
    required this.root,
    required this.worktreePath,
    required this.branch,
  });

  /// The chat session id a task is bound to -- isolation survives a multi-message conversation
  /// without re-provisioning, see `task_handler.dart`'s own docs.
  final String taskKey;
  final String taskId;

  /// Canonical path of the granted [WorkspaceRoot] this worktree was carved out of.
  final String root;
  final String worktreePath;
  final String branch;

  Map<String, Object?> toJson() => {
        'task_key': taskKey,
        'task_id': taskId,
        'root': root,
        'worktree_path': worktreePath,
        'branch': branch,
      };

  static TaskWorkspace fromJson(Map<String, dynamic> json) => TaskWorkspace(
        taskKey: json['task_key'] as String,
        taskId: json['task_id'] as String,
        root: json['root'] as String,
        worktreePath: json['worktree_path'] as String,
        branch: json['branch'] as String,
      );
}

/// Persists the ephemeral per-task worktree bindings [TaskHandler] holds in memory, so a daemon
/// restart does not lose track of a worktree/branch that still exists on disk -- without this, a
/// task's second chat message after a restart would look like `create_task` had never run for
/// that session, and either fail (an unknown `task_id`) or silently provision a second worktree
/// for the same task. Deliberately far simpler than [WorkspaceStore]: these are user-owned
/// directories already inside a granted root, not resources competing for host capacity like a
/// Docker sandbox, so there is no TTL/capacity policy here -- only "does this row still point at
/// something real," which [TaskHandler] itself validates on load.
class TaskWorkspaceStore {
  TaskWorkspaceStore(this._file);

  factory TaskWorkspaceStore.forConfigDir(Directory configDir) =>
      TaskWorkspaceStore(File('${configDir.path}/task_workspaces.json'));

  final File _file;

  List<TaskWorkspace> load() {
    if (!_file.existsSync()) return const [];
    final raw = _file.readAsStringSync();
    if (raw.trim().isEmpty) return const [];
    final decoded = jsonDecode(raw);
    if (decoded is! List) return const [];
    return decoded
        .whereType<Map<String, dynamic>>()
        .map(TaskWorkspace.fromJson)
        .toList(growable: false);
  }

  void save(List<TaskWorkspace> entries) {
    _file.parent.createSync(recursive: true);
    _file.writeAsStringSync(jsonEncode(entries.map((e) => e.toJson()).toList()));
  }
}
