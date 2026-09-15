/// Implements the RPC methods the backend's `HostAgentSandboxRunner`
/// (`aura_backend/aura_core/adapters/host_agent.py`) calls: `create_task`, `run`, `write_file`,
/// `read_file`. Every filesystem/command operation goes through [WorkspaceRoots] for containment
/// before touching disk -- this class has no path-handling logic of its own that bypasses it.
///
/// A "task" here does not own a single workspace root the way a Docker sandbox owns one
/// container: paths arrive workspace-relative from the tool layer's point of view, so each call
/// resolves against whichever granted root actually contains it (`findContainingRoot`). A path
/// outside every granted root is refused outright, matching ADR-011's "never merely confirmable."
///
/// D8 (`docs/architecture/015-desktop-ide.md` Part B) is the one exception to "no single owned
/// root": `create_task` called with `isolate: true` carves a real `git worktree` out of a
/// granted root and binds the returned `task_id` to it. From that point on, every call carrying
/// that `task_id` resolves *only* against the worktree -- see `_resolveTaskRoot`'s own doc
/// comment for why that must never fall back to the cross-all-roots search above.
library;

import 'dart:convert';
import 'dart:io';

import 'approval_queue.dart';
import 'command_policy.dart';
import 'containment.dart';
import 'diff_util.dart';
import 'git_service.dart';
import 'workspace_store.dart';

class RpcError implements Exception {
  RpcError(this.message);
  final String message;

  @override
  String toString() => message;
}

/// How long a command may run before this side kills it -- a hard ceiling under whatever
/// `timeout_s` the tool call requested, in case the caller asks for something unreasonable.
const _maxCommandTimeout = Duration(minutes: 15);

/// Bounds how much of a command's output this daemon ever holds in memory or forwards over the
/// wire, independent of whatever cap the backend applies on its own end while draining a streamed
/// run (`aura_backend/aura_core/tools/coding.py::_RUN_COMMAND_STREAM_CAP_CHARS`). This is the
/// "enforce output caps... on the daemon side too, not only server-side" half of that job -- a
/// runaway command should not be able to grow this process's memory or flood the connection
/// before either side's truncation ever gets a chance to apply. Once reached, the process's
/// stdout/stderr are still drained (never reading them again would let the pipe fill and block
/// the child, recreating the same kind of hang the timeout-kill path already had to fix), just no
/// longer captured or forwarded.
const _maxOutputCapChars = 500000;

/// Called once per chunk of output as a running command produces it (`stream` being `"stdout"` or
/// `"stderr"`). Optional: [TaskHandler.run] behaves identically without one, just without giving
/// the caller a way to observe output before the command finishes.
typedef OutputChunkCallback = void Function(String stream, String data);

/// One D8 per-task git worktree bound into memory -- see `createWorktree`'s doc comment on
/// [GitService] and `docs/architecture/015-desktop-ide.md` Part B. Two things about this are
/// deliberate: it is keyed by *both* [taskId] (how backend RPCs reference it after `create_task`
/// returns) and [taskKey] (the chat session it belongs to, for idempotent reuse across messages
/// in that session), and [worktreeRoot] is never added to the daemon's persisted
/// [WorkspaceRoots]/`workspaces.json` -- it must never appear in the Explorer's granted-roots UI
/// or survive as a normal grant, only as this in-memory (and [TaskWorkspaceStore]-persisted)
/// binding.
class _TaskWorktree {
  _TaskWorktree({
    required this.taskId,
    required this.taskKey,
    required this.parentRoot,
    required this.worktreeRoot,
    required this.branch,
  });

  final String taskId;
  final String taskKey;
  final WorkspaceRoot parentRoot;
  final WorkspaceRoot worktreeRoot;
  final String branch;
}

class TaskHandler {
  TaskHandler(
    this._roots, [
    ApprovalQueue? approvalQueue,
    GitService? git,
    TaskWorkspaceStore? taskWorkspaceStore,
  ])  : _approvals = approvalQueue ?? ApprovalQueue(),
        _git = git ?? GitService(),
        _taskStore = taskWorkspaceStore {
    _restoreTaskWorktrees();
  }

  final WorkspaceRoots _roots;

  /// D5's interactive consent queue -- see `approval_queue.dart`. Defaults to a private instance
  /// when not supplied, so every pre-D5 call site (a single positional `TaskHandler(roots)`, in
  /// tests and in `bin/aura_hostd.dart`) keeps compiling unchanged; `bin/aura_hostd.dart` passes
  /// the same queue instance `ControlServer` uses, so `GET /approvals` sees what `run` queues.
  final ApprovalQueue _approvals;
  final Set<String> _activeTaskIds = {};

  /// D8 (`docs/architecture/015-desktop-ide.md` Part B): git execution for provisioning/tearing
  /// down a task's isolated worktree. A private instance by default so every pre-D8 call site
  /// keeps compiling unchanged, exactly like [_approvals] above.
  final GitService _git;

  /// Durable bookkeeping for the maps below -- `null` (the pre-D8 default and every test that
  /// does not exercise isolation) simply means bindings do not survive a restart, which matches
  /// this class's pre-D8 behaviour exactly (nothing did).
  final TaskWorkspaceStore? _taskStore;

  final Map<String, _TaskWorktree> _byTaskId = {};
  final Map<String, _TaskWorktree> _byTaskKey = {};

  /// Best-effort self-healing on daemon startup: a worktree/branch created in a previous process
  /// lifetime still exists on disk, so a restart should not orphan it or force a fresh
  /// `create_task` (which would silently leave two worktrees for the same task). Entries whose
  /// parent root is no longer granted, or whose worktree directory no longer exists (the human
  /// deleted it, or `discard_task_workspace` ran while this daemon instance was down and only
  /// partially completed), are dropped rather than trusted -- a stale row is worse than an absent
  /// one, since [TaskHandler.writeFile]/[run] would otherwise resolve into a directory that is no
  /// longer there.
  void _restoreTaskWorktrees() {
    final store = _taskStore;
    if (store == null) return;
    final saved = store.load();
    final survivors = <TaskWorkspace>[];
    for (final entrySaved in saved) {
      final parentRoot = _roots.findContainingRoot(entrySaved.root);
      if (parentRoot == null || !Directory(entrySaved.worktreePath).existsSync()) continue;
      final entry = _TaskWorktree(
        taskId: entrySaved.taskId,
        taskKey: entrySaved.taskKey,
        parentRoot: parentRoot,
        worktreeRoot: WorkspaceRoot(canonicalPath: entrySaved.worktreePath, trusted: parentRoot.trusted),
        branch: entrySaved.branch,
      );
      _byTaskId[entry.taskId] = entry;
      _byTaskKey[entry.taskKey] = entry;
      _activeTaskIds.add(entry.taskId);
      survivors.add(entrySaved);
    }
    if (survivors.length != saved.length) {
      store.save(survivors);
    }
  }

  void _persistTaskWorktrees() {
    _taskStore?.save(
      _byTaskId.values
          .map((e) => TaskWorkspace(
                taskKey: e.taskKey,
                taskId: e.taskId,
                root: e.parentRoot.canonicalPath,
                worktreePath: e.worktreeRoot.canonicalPath,
                branch: e.branch,
              ))
          .toList(growable: false),
    );
  }

  Future<Map<String, Object?>> createTask(Map<String, dynamic> params) async {
    if (params['isolate'] != true) {
      final taskId = 't_${DateTime.now().microsecondsSinceEpoch}_${_activeTaskIds.length}';
      _activeTaskIds.add(taskId);
      return {'task_id': taskId};
    }

    final taskKey = _requireString(params, 'task_key');
    final existing = _byTaskKey[taskKey];
    if (existing != null) {
      // "Task" = one chat session, not one turn: a second message in the same session reuses the
      // worktree/branch already provisioned for it rather than re-isolating or losing context.
      return {
        'task_id': existing.taskId,
        'root': existing.worktreeRoot.canonicalPath,
        'parent_root': existing.parentRoot.canonicalPath,
        'branch': existing.branch,
      };
    }

    final rawRoot = params['root'] as String?;
    final parentRoot = rawRoot != null
        ? _roots.findContainingRoot(rawRoot)
        : (_roots.all.length == 1 ? _roots.all.first : null);
    if (parentRoot == null) {
      throw RpcError(
        rawRoot != null
            ? 'no granted workspace root contains "$rawRoot"'
            : 'cannot isolate a task without an explicit "root" -- more than one workspace root '
                'is granted',
      );
    }

    final taskId = 't_${DateTime.now().microsecondsSinceEpoch}_${_activeTaskIds.length}';
    _activeTaskIds.add(taskId);
    final branchName = 'aura/task-$taskId';
    final worktree = await _git.createWorktree(
      parentRoot.canonicalPath,
      taskId: taskId,
      branchName: branchName,
      baseBranch: params['base_branch'] as String?,
    );
    // Confirmed scope: a task's ephemeral root inherits the parent project's trust level exactly
    // -- no widening (a task in an untrusted project is not a backdoor to trusted execution) and
    // no narrowing (no new approval friction for a project the human already trusted).
    final worktreeRoot = WorkspaceRoot(canonicalPath: worktree.path, trusted: parentRoot.trusted);
    final entry = _TaskWorktree(
      taskId: taskId,
      taskKey: taskKey,
      parentRoot: parentRoot,
      worktreeRoot: worktreeRoot,
      branch: branchName,
    );
    _byTaskId[taskId] = entry;
    _byTaskKey[taskKey] = entry;
    _persistTaskWorktrees();
    // `parent_root` is what a caller must send back as `reopen_task`'s own `root` param -- the
    // granted root the worktree was carved out of, not the worktree path itself (which this
    // response's `root` field carries, matching every other RPC's use of that name).
    return {
      'task_id': taskId,
      'root': worktreeRoot.canonicalPath,
      'parent_root': parentRoot.canonicalPath,
      'branch': branchName,
    };
  }

  /// Re-registers an isolated task's in-memory binding after this daemon has forgotten it (a
  /// restart this instance's own [_restoreTaskWorktrees] could not self-heal -- e.g. the backend
  /// remembers a task via its own durable `HostTaskStore` but this daemon's local
  /// `task_workspaces.json` does not, or never did) without running `git worktree add` again,
  /// which would fail outright against a branch name that already exists. Refuses if the
  /// worktree directory the caller names is no longer actually there -- the caller
  /// (`HostAgentSandboxRunner`) is expected to fall back to a fresh `create_task` in that case,
  /// not treat this as a partial success.
  Future<Map<String, Object?>> reopenTask(Map<String, dynamic> params) async {
    final taskKey = _requireString(params, 'task_key');
    final taskId = _requireString(params, 'task_id');
    final worktreePath = _requireString(params, 'worktree_path');
    final rootPath = _requireString(params, 'root');
    final branch = _requireString(params, 'branch');

    final byId = _byTaskId[taskId];
    if (byId != null) {
      return {
        'task_id': byId.taskId,
        'root': byId.worktreeRoot.canonicalPath,
        'parent_root': byId.parentRoot.canonicalPath,
        'branch': byId.branch,
      };
    }

    final parentRoot = _roots.findContainingRoot(rootPath);
    if (parentRoot == null) {
      throw RpcError('workspace root "$rootPath" is no longer granted -- cannot reopen task "$taskId"');
    }
    if (!Directory(worktreePath).existsSync()) {
      throw RpcError('worktree "$worktreePath" no longer exists on disk -- create a new task instead');
    }
    final entry = _TaskWorktree(
      taskId: taskId,
      taskKey: taskKey,
      parentRoot: parentRoot,
      worktreeRoot: WorkspaceRoot(
        canonicalPath: Directory(worktreePath).resolveSymbolicLinksSync(),
        trusted: parentRoot.trusted,
      ),
      branch: branch,
    );
    _byTaskId[taskId] = entry;
    _byTaskKey[taskKey] = entry;
    _activeTaskIds.add(taskId);
    _persistTaskWorktrees();
    return {
      'task_id': taskId,
      'root': entry.worktreeRoot.canonicalPath,
      'parent_root': entry.parentRoot.canonicalPath,
      'branch': branch,
    };
  }

  /// Tears down an isolated task's worktree and branch. Deliberately **not** reachable from
  /// [HostAgentClient]'s agent-facing RPC dispatch (`rpc_client.dart`) -- this is a human-only
  /// action, wired into [ControlServer] instead (D9 plan section C.3: "invoked only by a human's
  /// explicit... action, never by the agent"), matching [GitService]'s own human-only-write
  /// precedent.
  Future<Map<String, Object?>> discardTaskWorkspace(String taskId) async {
    final entry = _byTaskId.remove(taskId);
    if (entry == null) {
      throw RpcError('unknown task "$taskId"');
    }
    _byTaskKey.remove(entry.taskKey);
    await _git.removeWorktree(entry.parentRoot.canonicalPath, entry.worktreeRoot.canonicalPath, entry.branch);
    _persistTaskWorktrees();
    return {'ok': true};
  }

  /// Resolves the isolated [WorkspaceRoot] a call is bound to, if it names a `task_id` --
  /// returns `null` for a call with no `task_id` (every pre-D8 caller). Once a `task_id` is
  /// bound, every write/run for it must resolve **only** against that root: this deliberately
  /// throws rather than returning `null` for an unrecognised `task_id`, so [writeFile]/
  /// [writeFiles]/[readFile]/[run] never silently fall back to a cross-all-roots search that
  /// could resolve an isolated call's path into the live checkout via a differently-granted root
  /// that happens to also contain it (D8 plan section B.2, point 3).
  WorkspaceRoot? _resolveTaskRoot(Map<String, dynamic> params) {
    final taskId = params['task_id'] as String?;
    if (taskId == null) return null;
    final entry = _byTaskId[taskId];
    if (entry == null) {
      throw RpcError(
        'unknown task "$taskId" -- it may have been discarded, or this daemon restarted and '
        'forgot it; call create_task (or reopen_task) again',
      );
    }
    return entry.worktreeRoot;
  }

  /// D6 plan section C.3 (the write-gating retrofit): the agent-facing write RPC now goes through
  /// the same trust/approval-queue gate `run()` already had since D5 -- previously this and
  /// `edit_file` (which routes through this same RPC on the backend, see `coding.py::edit_file`)
  /// were the one place an agent could freely mutate any file in a granted-but-untrusted root.
  Future<Map<String, Object?>> writeFile(Map<String, dynamic> params) async {
    final path = _requireString(params, 'path');
    final content = _requireString(params, 'content');
    final root = _resolveTaskRoot(params) ?? _roots.findContainingRoot(path);
    if (root == null) {
      throw RpcError(
        'no granted workspace root contains "$path" -- grant it (or its parent project folder) '
        'first',
      );
    }
    final resolved = _roots.requireContained(root, path);

    final verdict = evaluateWrite(workspaceTrusted: root.trusted);
    if (verdict.decision == CommandDecision.needsApproval) {
      await _awaitWriteApproval([WriteOp(path: path, finalContent: content)], cwd: root.canonicalPath, root: root);
    }

    final file = File(resolved);
    file.parent.createSync(recursive: true);
    file.writeAsStringSync(content);
    return {'bytes_written': utf8.encode(content).length};
  }

  /// D6 plan section B.3: atomically write or find/replace-edit multiple files. Pre-validates
  /// every operation (containment, and -- for a find/replace op -- that the find-text exists in
  /// the target exactly once) before writing anything; if any operation fails validation, nothing
  /// in the call is written (design decision 4: no rollback machinery needed, since writes only
  /// start once every operation is already known-good). All operations in one call must resolve
  /// within the same granted root -- a cross-root atomic batch would need per-operation trust
  /// gating, which is out of this phase's scope.
  Future<Map<String, Object?>> writeFiles(Map<String, dynamic> params) async {
    final rawOps = params['operations'];
    if (rawOps is! List || rawOps.isEmpty) {
      throw RpcError('"operations" must be a non-empty list');
    }

    final taskRoot = _resolveTaskRoot(params);
    WorkspaceRoot? sharedRoot = taskRoot;
    final resolved = <WriteOp>[];
    for (final raw in rawOps) {
      if (raw is! Map) {
        throw RpcError('each operation must be an object');
      }
      final op = Map<String, dynamic>.from(raw);
      final path = _requireString(op, 'path');
      final root = taskRoot ?? _roots.findContainingRoot(path);
      if (root == null) {
        throw RpcError('no granted workspace root contains "$path"');
      }
      if (sharedRoot == null) {
        sharedRoot = root;
      } else if (root.canonicalPath != sharedRoot.canonicalPath) {
        throw RpcError(
          'all operations in a single write_files call must resolve within the same granted '
          'workspace root ("$path" resolves under a different root than earlier operations)',
        );
      }
      final absolute = _roots.requireContained(root, path);

      final String finalContent;
      if (op.containsKey('content')) {
        finalContent = _requireString(op, 'content');
      } else if (op.containsKey('find') && op.containsKey('replace')) {
        final find = _requireString(op, 'find');
        final replace = _requireString(op, 'replace');
        if (!File(absolute).existsSync()) {
          throw RpcError('"$path" does not exist');
        }
        final current = File(absolute).readAsStringSync();
        final occurrences = _countOccurrences(current, find);
        if (occurrences != 1) {
          throw RpcError(
            'expected exactly one occurrence of the given text in "$path", found $occurrences',
          );
        }
        finalContent = current.replaceFirst(find, replace);
      } else {
        throw RpcError('each operation must have either "content", or both "find" and "replace"');
      }
      resolved.add(WriteOp(path: path, finalContent: finalContent));
    }

    final root = sharedRoot!;
    final verdict = evaluateWrite(workspaceTrusted: root.trusted);
    if (verdict.decision == CommandDecision.needsApproval) {
      await _awaitWriteApproval(resolved, cwd: root.canonicalPath, root: root);
    }

    var totalBytes = 0;
    for (final op in resolved) {
      final absolute = _roots.requireContained(root, op.path);
      final file = File(absolute);
      file.parent.createSync(recursive: true);
      file.writeAsStringSync(op.finalContent);
      totalBytes += utf8.encode(op.finalContent).length;
    }
    return {'bytes_written': totalBytes};
  }

  Future<Map<String, Object?>> readFile(Map<String, dynamic> params) async {
    final path = _requireString(params, 'path');
    final root = _resolveTaskRoot(params) ?? _roots.findContainingRoot(path);
    if (root == null) {
      throw RpcError('no granted workspace root contains "$path"');
    }
    final resolved = _roots.requireContained(root, path);
    final file = File(resolved);
    if (!file.existsSync()) {
      throw RpcError('$path does not exist');
    }
    return {'content': file.readAsStringSync()};
  }

  Future<Map<String, Object?>> run(
    Map<String, dynamic> params, {
    OutputChunkCallback? onChunk,
  }) async {
    // The caller (`aura_backend/aura_core/tools/coding.py::run_command`) always sends
    // `["sh", "-c", "<shell command>"]` -- a full argv, not a bare string -- so it can be
    // `Process.start`ed directly without this daemon re-interpreting it through another shell.
    // Tolerate other argv shapes too (a future caller might not go through `sh -c`) by falling
    // back to running the argv's first element as the program.
    final commandArgv = _requireStringList(params, 'command');
    if (commandArgv.isEmpty) {
      throw RpcError('"command" must be a non-empty list of arguments');
    }
    final cwdParam = params['cwd'] as String?; // not sent by the current backend; tolerated
    final requestedTimeout = (params['timeout_s'] as num?)?.toDouble() ?? 60.0;

    final taskRoot = _resolveTaskRoot(params);
    final root = taskRoot ??
        (cwdParam != null
            ? _roots.findContainingRoot(cwdParam)
            : (_roots.all.isNotEmpty ? _roots.all.first : null));
    if (root == null) {
      throw RpcError(
        cwdParam != null
            ? 'no granted workspace root contains "$cwdParam"'
            : 'no workspace root is granted -- run `aura_hostd grant <path>` first',
      );
    }
    final cwd = cwdParam != null ? _roots.requireContained(root, cwdParam) : root.canonicalPath;

    final commandForPolicy = _effectiveShellCommand(commandArgv);
    final verdict = evaluateCommand(commandForPolicy, workspaceTrusted: root.trusted);
    if (verdict.decision == CommandDecision.deny) {
      throw RpcError('command refused: ${verdict.reason}');
    }
    if (verdict.decision == CommandDecision.needsApproval) {
      await _awaitApproval(commandForPolicy, cwd: cwd, root: root);
    }

    final timeout = Duration(
      milliseconds: (requestedTimeout * 1000).clamp(1000, _maxCommandTimeout.inMilliseconds).round(),
    );

    final effectiveArgv = _lineBuffered(commandArgv);
    final process = await Process.start(
      effectiveArgv.first,
      effectiveArgv.sublist(1),
      workingDirectory: cwd,
    );

    final stdoutBuf = StringBuffer();
    final stderrBuf = StringBuffer();
    var capturedChars = 0;
    var outputTruncated = false;

    void capture(String streamName, StringBuffer buf, String chunk) {
      if (capturedChars >= _maxOutputCapChars) {
        outputTruncated = true;
        return; // still drained below, just no longer kept or forwarded -- see field doc.
      }
      final remaining = _maxOutputCapChars - capturedChars;
      final piece = chunk.length > remaining ? chunk.substring(0, remaining) : chunk;
      buf.write(piece);
      capturedChars += piece.length;
      if (piece.isNotEmpty) onChunk?.call(streamName, piece);
      if (piece.length < chunk.length) outputTruncated = true;
    }

    final stdoutDone = process.stdout
        .transform(const SystemEncoding().decoder)
        .forEach((chunk) => capture('stdout', stdoutBuf, chunk));
    final stderrDone = process.stderr
        .transform(const SystemEncoding().decoder)
        .forEach((chunk) => capture('stderr', stderrBuf, chunk));

    int exitCode;
    var timedOut = false;
    try {
      exitCode = await process.exitCode.timeout(timeout, onTimeout: () {
        timedOut = true;
        process.kill(ProcessSignal.sigkill);
        return -1;
      });
    } finally {
      // Killing `process` only signals the PID we started -- `sh -c '<command>'` may have forked
      // rather than exec'd it (shell-dependent, not something this daemon controls), leaving an
      // orphaned descendant that still holds the stdout/stderr pipe fds open. Waiting on
      // stdoutDone/stderrDone unboundedly would then hang this whole call until that orphan exits
      // on its own -- exactly as long as whatever it was doing (observed: a killed `sleep 30`
      // held the pipes open for the full 30 seconds). Bounding this wait is the fix: a normally
      // terminating process's streams close well within this window, and a still-running orphan
      // does not get to stall the RPC response it's no longer even attached to.
      await Future.wait([stdoutDone, stderrDone]).timeout(
        const Duration(seconds: 2),
        onTimeout: () => const <void>[],
      );
    }

    if (timedOut) {
      stderrBuf.write('\n[aura_hostd] command killed after ${timeout.inSeconds}s timeout');
    }
    if (outputTruncated) {
      stderrBuf.write('\n[aura_hostd] output truncated after $_maxOutputCapChars characters');
    }

    return {
      'stdout': stdoutBuf.toString(),
      'stderr': stderrBuf.toString(),
      'exit_code': exitCode,
    };
  }

  /// Queues [command] for interactive approval and blocks until a human answers or it times out
  /// (`approval_queue.dart`'s [approvalTimeout]). On approval, re-derives the command's identity
  /// hash from the *current* call context and compares it against the hash the approval was
  /// bound to at request time -- if the root this [cwd] resolves under has changed (e.g. revoked)
  /// since the request was queued, the hashes diverge and this refuses to run rather than
  /// executing something the human no longer sees the same context for (D5 plan design decision
  /// 5). Throws [RpcError] on rejection, expiry, or a revalidation mismatch; returns normally only
  /// when the exact operation that was approved is still safe to run.
  Future<void> _awaitApproval(String command, {required String cwd, required WorkspaceRoot root}) async {
    final approval = _approvals.createForCommand(canonicalCommand: command, cwd: cwd, rootPath: root.canonicalPath);
    final status = await _approvals.awaitDecision(approval);
    switch (status) {
      case ApprovalStatus.approved:
        break;
      case ApprovalStatus.rejected:
        throw RpcError('command was rejected by the user: $command');
      case ApprovalStatus.expired:
        throw RpcError('command approval timed out waiting for a response: $command');
      case ApprovalStatus.cancelled:
        throw RpcError('command approval was cancelled: $command');
      case ApprovalStatus.pending:
        throw RpcError('command approval resolved with no decision -- this should not happen');
    }
    final currentRoot = _roots.findContainingRoot(cwd);
    final currentHash = currentRoot == null
        ? null
        : PendingApproval.hashForCommand(command: command, cwd: cwd, rootPath: currentRoot.canonicalPath);
    if (currentHash != approval.operationHash) {
      throw RpcError(
        'command context changed since it was approved (workspace root no longer matches) -- '
        'refusing to run: $command',
      );
    }
  }

  /// Queues [operations] for interactive approval and blocks until a human answers or it times
  /// out, mirroring [_awaitApproval]'s shape for commands. Generates the diff [operations] would
  /// produce (reading each target's current on-disk content -- empty for a new file -- before the
  /// approval is created, so the diff a human reviews matches [PendingApproval.diffPreview]
  /// exactly) and captures a snapshot of that "before" content per path.
  ///
  /// On approval, re-reads each path's *current* on-disk content and compares it against the
  /// snapshot captured when the diff was generated -- not a comparison of [operations] against
  /// itself, which could never detect anything (the intended new content doesn't change while
  /// waiting). This is the write-specific staleness a command doesn't have: the file(s) can
  /// themselves change on disk between request and approval (the human edits it, or another write
  /// lands first), so what must be revalidated is "does the approved diff still describe reality,"
  /// not just "is the root still granted" (D6 plan section C.3).
  Future<void> _awaitWriteApproval(
    List<WriteOp> operations, {
    required String cwd,
    required WorkspaceRoot root,
  }) async {
    final beforeSnapshots = <String, String>{};
    final diffParts = <String>[];
    for (final op in operations) {
      final absolute = _roots.requireContained(root, op.path);
      final before = File(absolute).existsSync() ? File(absolute).readAsStringSync() : '';
      beforeSnapshots[op.path] = before;
      diffParts.add(unifiedDiff(path: op.path, before: before, after: op.finalContent));
    }

    final approval = _approvals.createForWrite(
      operations: operations,
      diffText: diffParts.join('\n'),
      cwd: cwd,
      rootPath: root.canonicalPath,
    );
    final pathList = operations.map((o) => o.path).join(', ');
    final status = await _approvals.awaitDecision(approval);
    switch (status) {
      case ApprovalStatus.approved:
        break;
      case ApprovalStatus.rejected:
        throw RpcError('write was rejected by the user: $pathList');
      case ApprovalStatus.expired:
        throw RpcError('write approval timed out waiting for a response: $pathList');
      case ApprovalStatus.cancelled:
        throw RpcError('write approval was cancelled: $pathList');
      case ApprovalStatus.pending:
        throw RpcError('write approval resolved with no decision -- this should not happen');
    }

    final currentRoot = _roots.findContainingRoot(cwd);
    if (currentRoot == null) {
      throw RpcError('workspace root is no longer granted -- refusing approved write: $pathList');
    }
    for (final op in operations) {
      final absolute = _roots.requireContained(currentRoot, op.path);
      final current = File(absolute).existsSync() ? File(absolute).readAsStringSync() : '';
      if (current != beforeSnapshots[op.path]) {
        throw RpcError(
          'file "${op.path}" changed on disk since this write was approved -- the approved diff '
          'no longer reflects the current file, refusing to write',
        );
      }
    }
  }

  int _countOccurrences(String haystack, String needle) {
    if (needle.isEmpty) return 0;
    var count = 0;
    var index = 0;
    while (true) {
      final found = haystack.indexOf(needle, index);
      if (found == -1) break;
      count++;
      index = found + needle.length;
    }
    return count;
  }

  String _requireString(Map<String, dynamic> params, String key) {
    final value = params[key];
    if (value is! String) {
      throw RpcError('missing or invalid "$key" parameter');
    }
    return value;
  }

  List<String> _requireStringList(Map<String, dynamic> params, String key) {
    final value = params[key];
    if (value is! List) {
      throw RpcError('missing or invalid "$key" parameter');
    }
    return value.map((e) => e.toString()).toList(growable: false);
  }
}

/// `["sh", "-c", "<command>"]` / `["cmd.exe", "/c", "<command>"]` -> `"<command>"`, so command
/// policy evaluates the actual shell command the user's tool call asked for rather than the shell
/// wrapper around it. Any other shape is joined with spaces as a best-effort fallback -- it will
/// not match the allowlist, which fails safe (denied, not silently allowed).
String _effectiveShellCommand(List<String> argv) {
  final looksLikeShellWrapper = argv.length == 3 &&
      (argv[0] == 'sh' || argv[0] == 'bash' || argv[0] == 'cmd.exe') &&
      (argv[1] == '-c' || argv[1] == '/c');
  return looksLikeShellWrapper ? argv[2] : argv.join(' ');
}

bool? _stdbufAvailableCache;

/// Checked once (cheap insurance against a `PATH` lookup on every single command) and cached for
/// the daemon's lifetime -- `stdbuf` is not going to appear or disappear mid-run.
bool _stdbufAvailable() {
  final cached = _stdbufAvailableCache;
  if (cached != null) return cached;
  if (Platform.isWindows) {
    return _stdbufAvailableCache = false;
  }
  try {
    final result = Process.runSync('which', ['stdbuf']);
    return _stdbufAvailableCache = result.exitCode == 0;
  } catch (_) {
    return _stdbufAvailableCache = false;
  }
}

/// This is the daemon's PTY *approximation*, not a real one -- there is no raw terminal mode, no
/// resize, no job control, and no `isatty()` lie to the child process (a program that changes its
/// output format based on whether stdout is a terminal will still see a pipe and behave
/// accordingly). What it does provide: `stdbuf -oL -eL` forces line buffering instead of glibc's
/// default full buffering when stdout/stderr are not a terminal, so `run`'s streamed output
/// actually shows real incremental progress for common C-based CLI tools (git, grep, make, ...)
/// instead of one big flush at the end. It works through `sh -c '<command>'` because the
/// LD_PRELOAD environment variable `stdbuf` sets is inherited by the shell's own child process,
/// not because `sh` itself needs unbuffering. It does **not** help an interpreted runtime that
/// decides its own stdout buffering independent of libc's setvbuf (Python's own io layer is the
/// common case; `PYTHONUNBUFFERED=1` is that ecosystem's equivalent, and is not set here since
/// this daemon should not be guessing per-runtime environment variables on the user's behalf).
/// Falls back to the argv unchanged when `stdbuf` is not on `PATH` (any non-Linux machine without
/// GNU coreutils, most notably macOS's default install).
List<String> _lineBuffered(List<String> argv) {
  if (!_stdbufAvailable()) return argv;
  return ['stdbuf', '-oL', '-eL', ...argv];
}
