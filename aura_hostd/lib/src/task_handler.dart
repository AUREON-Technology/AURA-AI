/// Implements the RPC methods the backend's `HostAgentSandboxRunner`
/// (`aura_backend/aura_core/adapters/host_agent.py`) calls: `create_task`, `run`, `write_file`,
/// `read_file`. Every filesystem/command operation goes through [WorkspaceRoots] for containment
/// before touching disk -- this class has no path-handling logic of its own that bypasses it.
///
/// A "task" here does not own a single workspace root the way a Docker sandbox owns one
/// container: paths arrive workspace-relative from the tool layer's point of view, so each call
/// resolves against whichever granted root actually contains it (`findContainingRoot`). A path
/// outside every granted root is refused outright, matching ADR-011's "never merely confirmable."
library;

import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;

import 'approval_queue.dart';
import 'command_policy.dart';
import 'containment.dart';
import 'diff_util.dart';
import 'git_service.dart';

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

class TaskHandler {
  TaskHandler(this._roots, [ApprovalQueue? approvalQueue, GitService? gitService])
      : _approvals = approvalQueue ?? ApprovalQueue(),
        _git = gitService ?? GitService();

  final WorkspaceRoots _roots;

  /// D5's interactive consent queue -- see `approval_queue.dart`. Defaults to a private instance
  /// when not supplied, so every pre-D5 call site (a single positional `TaskHandler(roots)`, in
  /// tests and in `bin/aura_hostd.dart`) keeps compiling unchanged; `bin/aura_hostd.dart` passes
  /// the same queue instance `ControlServer` uses, so `GET /approvals` sees what `run` queues.
  final ApprovalQueue _approvals;

  /// D8 Part B's isolated-task worktrees -- see `_resolveTaskRoot`. A second, independent
  /// `GitService` instance from `ControlServer`'s own (never shared): this one only ever runs
  /// `worktree add`/`remove`/`branch -D` on the agent's behalf, still gated by the same
  /// containment/trust rules every other agent-facing RPC in this class already enforces --
  /// unlike `ControlServer`'s `GitService`, which is the deliberate human-only bypass
  /// (`git_service.dart`'s own doc comment). Do not merge these two instances.
  final GitService _git;
  final Set<String> _activeTaskIds = {};

  /// task_id -> isolation config, recorded once at [createTask] time.
  final Map<String, _TaskConfig> _taskConfigs = {};

  /// task_id -> the ephemeral [WorkspaceRoot] resolved for it (set lazily, on the first call that
  /// actually needs a root -- see `_resolveTaskRoot`).
  final Map<String, WorkspaceRoot> _taskRoots = {};

  /// task_key (the chat session id, stable across a session's many turns and thus many task_ids)
  /// -> the isolated worktree already carved out for it. Keying by task_key rather than task_id
  /// is what makes isolation survive a multi-message conversation: the backend mints a fresh
  /// task_id per turn (`_SandboxSessionCache`, `aura_core/tools/coding.py`), but every turn in the
  /// same chat session sends the same task_key.
  final Map<String, _IsolatedTask> _taskKeyWorktrees = {};

  Future<Map<String, Object?>> createTask(Map<String, dynamic> params) async {
    final taskId = 't_${DateTime.now().microsecondsSinceEpoch}_${_activeTaskIds.length}';
    _activeTaskIds.add(taskId);

    final isolate = params['isolate'] == true;
    final taskKey = params['task_key'] as String?;
    if (isolate && taskKey != null) {
      _taskConfigs[taskId] = _TaskConfig(
        taskKey: taskKey,
        baseBranch: params['base_branch'] as String?,
      );
      // If this task_key already has a worktree (a later turn in the same chat session), bind
      // it immediately rather than waiting for the first write/run call to discover it.
      final existing = _taskKeyWorktrees[taskKey];
      if (existing != null) _taskRoots[taskId] = existing.root;
    }
    return {'task_id': taskId, 'isolated': isolate && taskKey != null};
  }

  /// Resolves the effective [WorkspaceRoot] a [taskId]'s call should use: an already-bound
  /// ephemeral root, a newly-discovered one (this task's first isolated call), or `null` to fall
  /// back to the caller's own ordinary cross-all-roots search -- unchanged behavior for every
  /// non-Desktop caller, since [taskId] is only ever isolated when [createTask] was asked to.
  ///
  /// [pathForRootLookup] is whatever path/cwd the actual RPC named -- used only to find *which*
  /// granted root the worktree should be carved out of; once found, every subsequent call for
  /// this task_id (any path) resolves against the worktree instead, never the search result
  /// again (see D8 plan Part B.2.3 -- a task_id bound to an ephemeral root must never fall back
  /// to the cross-all-roots search, or an isolated write could silently escape into the live
  /// checkout via a path that also happens to resolve under a persisted root).
  ///
  /// Split from [_needsTaskRootResolution]/an already-bound lookup rather than folding
  /// everything into one `async` method that every caller `await`s unconditionally: even a
  /// no-op `await` on an `async` function that returns synchronously still costs one microtask
  /// hop in Dart, which is enough to break call sites elsewhere in this codebase that check
  /// `ApprovalQueue.list()` synchronously right after calling `writeFile` *without* awaiting it
  /// (`test/task_handler_test.dart`'s write-gating tests) -- those rely on everything up to the
  /// approval being queued happening in the same synchronous stack frame as the call. Callers
  /// below only ever pay that hop for a task_id actually configured for isolation.
  WorkspaceRoot? _boundTaskRoot(String? taskId) => taskId == null ? null : _taskRoots[taskId];

  bool _needsTaskRootResolution(String? taskId) =>
      taskId != null && _taskConfigs.containsKey(taskId) && _taskRoots[taskId] == null;

  Future<WorkspaceRoot?> _resolveTaskRoot(String? taskId, String? pathForRootLookup) async {
    if (taskId == null) return null;
    final bound = _taskRoots[taskId];
    if (bound != null) return bound;

    final config = _taskConfigs[taskId];
    if (config == null) return null;
    final taskKey = config.taskKey;

    final existing = _taskKeyWorktrees[taskKey];
    if (existing != null) {
      _taskRoots[taskId] = existing.root;
      return existing.root;
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
    _taskKeyWorktrees[taskKey] =
        _IsolatedTask(root: ephemeralRoot, parentRootPath: parentRoot.canonicalPath, branch: branch);
    _taskRoots[taskId] = ephemeralRoot;
    return ephemeralRoot;
  }

  /// D9 primitive (not yet reachable from any route in this D8 pass -- Part C wires a human-only
  /// "Discard" action to this). Tears down the isolated worktree/branch for [taskKey] entirely.
  Future<Map<String, Object?>> discardTaskWorkspace(Map<String, dynamic> params) async {
    final taskKey = _requireString(params, 'task_key');
    final info = _taskKeyWorktrees.remove(taskKey);
    if (info == null) {
      throw RpcError('no isolated workspace is known for task_key "$taskKey"');
    }
    return _git.removeWorktree(info.parentRootPath, info.root.canonicalPath, branch: info.branch);
  }

  /// D6 plan section C.3 (the write-gating retrofit): the agent-facing write RPC now goes through
  /// the same trust/approval-queue gate `run()` already had since D5 -- previously this and
  /// `edit_file` (which routes through this same RPC on the backend, see `coding.py::edit_file`)
  /// were the one place an agent could freely mutate any file in a granted-but-untrusted root.
  Future<Map<String, Object?>> writeFile(Map<String, dynamic> params) async {
    final path = _requireString(params, 'path');
    final content = _requireString(params, 'content');
    final taskId = params['task_id'] as String?;
    var root = _boundTaskRoot(taskId);
    if (root == null && _needsTaskRootResolution(taskId)) {
      root = await _resolveTaskRoot(taskId, path);
    }
    root ??= _roots.findContainingRoot(path);
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
    final taskId = params['task_id'] as String?;
    // Resolved once, from the first operation's path, then reused for every operation below --
    // once a task_id is bound to an ephemeral root, every path in this batch must resolve only
    // against it (D8 plan Part B.2.3), never fall back to searching other granted roots.
    var taskRoot = _boundTaskRoot(taskId);
    if (taskRoot == null && _needsTaskRootResolution(taskId)) {
      final firstPath = rawOps.isNotEmpty && rawOps.first is Map
          ? (Map<String, dynamic>.from(rawOps.first as Map)['path'] as String?)
          : null;
      taskRoot = await _resolveTaskRoot(taskId, firstPath);
    }

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
    final taskId = params['task_id'] as String?;
    var root = _boundTaskRoot(taskId);
    if (root == null && _needsTaskRootResolution(taskId)) {
      root = await _resolveTaskRoot(taskId, path);
    }
    root ??= _roots.findContainingRoot(path);
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
    final taskId = params['task_id'] as String?;

    var root = _boundTaskRoot(taskId);
    if (root == null && _needsTaskRootResolution(taskId)) {
      root = await _resolveTaskRoot(taskId, cwdParam);
    }
    root ??= cwdParam != null
        ? _roots.findContainingRoot(cwdParam)
        : (_roots.all.isNotEmpty ? _roots.all.first : null);
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

/// D8 Part B: recorded once per task_id at [TaskHandler.createTask] time, so a task's later
/// write/run calls (which only ever carry `task_id`, not the original `isolate`/`task_key`
/// params) can still find their way to the right worktree.
class _TaskConfig {
  const _TaskConfig({required this.taskKey, this.baseBranch});
  final String taskKey;
  final String? baseBranch;
}

/// D8 Part B: one isolated task's worktree, keyed by task_key in
/// [TaskHandler._taskKeyWorktrees] -- [parentRootPath]/[branch] are kept alongside [root] so
/// [TaskHandler.discardTaskWorkspace] (D9) can tear both down without having to re-derive them.
class _IsolatedTask {
  const _IsolatedTask({required this.root, required this.parentRootPath, required this.branch});
  final WorkspaceRoot root;
  final String parentRootPath;
  final String branch;
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
