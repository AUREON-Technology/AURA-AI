/// Implements the RPC methods the backend's `HostAgentSandboxRunner`
/// (`aura_backend/aura_core/adapters/host_agent.py`) calls: `create_task`, `run`, `write_file`,
/// `write_files`, `read_file`, `list_roots` and `init_git` (plus the `cancel` frame,
/// `rpc_client.dart`). Every
/// filesystem/command operation goes through [WorkspaceRoots] for containment
/// before touching disk -- this class has no path-handling logic of its own that bypasses it.
///
/// Remote control (WP8; ADR-011, D-10/D-10b/D-10c). `create_task` takes an `origin`: `"desktop"`
/// (also when absent, as older backends send it) or `"remote"`, a turn started on the phone or the
/// web. A remote task is refused unless this computer's `remote_control` flag (`config.json`, set
/// only through the local control server) is on, and that flag is re-read on every later call of
/// the task, so switching it off stops the next one. A remote task never gets the trusted tier,
/// the read-only allowlist or the desktop's approval queue -- nobody is at the desk: what is
/// refused in every tier stays refused, and every other `run`/`write_file`/`write_files` needs
/// `confirmed: true`, the backend's word that the person approved this exact call on the remote
/// device; without it the call fails with `confirmation_required:`. `read_file` needs no
/// confirmation. A desktop task ignores `confirmed`: the backend cannot waive the desktop's own
/// consent. The origin defends against a stolen phone or web session with an honest backend, not
/// against a compromised backend (which could simply claim `desktop`); the local opt-in and the
/// containment, denylist and `.git` rules are what hold then.
///
/// Isolation needs git (WP4, D-13). An isolated task (`isolate: true` with a `task_key`, which
/// the client sends for every Engineer task) works on its own worktree of the folder; in a folder
/// that is not a git work tree with a commit it is refused with `isolation_needs_git` -- never a
/// silent fallback to the live folder. `list_roots` says which folders can hold one (`is_git`),
/// and `init_git` -- the phone's or web's "Initialize git here", so it needs remote control on and
/// `confirmed: true` -- makes a granted folder a repository with a first commit (`git_init.dart`).
/// A task without isolation (older clients) works in the folder itself, as before.
///
/// A "task" here does not own a single workspace root the way a Docker sandbox owns one
/// container: paths arrive workspace-relative from the tool layer's point of view, so each call
/// resolves against whichever granted root actually contains it (`findContainingRoot`). A path
/// outside every granted root is refused outright, matching ADR-011's "never merely confirmable."
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;

import 'approval_queue.dart';
import 'command_policy.dart';
import 'containment.dart';
import 'diff_util.dart';
import 'error_codes.dart';
import 'git_init.dart';
import 'git_service.dart';
import 'isolated_task_registry.dart';
import 'process_env.dart';
import 'process_tree.dart';
import 'workspace_store.dart';

class RpcError implements Exception {
  RpcError(this.message, {this.code, this.detail});
  final String message;

  /// The underlying error alone (git's own message for `git_init_failed`), sent as the RPC
  /// `error` frame's `detail` so a translated message can quote it; null when there is none.
  final String? detail;

  /// Stable machine code (`error_codes.dart`) for a refusal, sent as the RPC `error` frame's
  /// `code` next to [message]; null for errors that are not refusals.
  final String? code;

  @override
  String toString() => message;
}

/// The error every call of a remote task gets while this computer's remote control is off (WP8).
/// The `remote_control_disabled:` prefix is what the backend and the apps match on; the rest is
/// shown to the person.
const remoteControlDisabledMessage = 'remote_control_disabled: Remote control is off on this '
    "computer. Turn on 'Allow control from my phone and the web' in AURA on the computer.";

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

/// The decoder for a command's stdout and stderr: UTF-8 on every platform, malformed bytes as U+FFFD.
///
/// A command's output is not guaranteed to be text: `cat report.pdf`, or a tool printing a legacy code
/// page, puts bytes that are not valid UTF-8 on the pipe. `SystemEncoding().decoder` is a *strict*
/// UTF-8 decoder on POSIX, so the first such byte threw a `FormatException` inside the stream listener
/// and the whole `run` RPC failed ("Invalid UTF-8 byte (at offset 7)"): the command's output was lost
/// and nothing kept reading the pipe, so a still-running command stalled on a full pipe until its
/// timeout. On Windows `SystemEncoding` is the ANSI code page, which turned "café" and Arabic into
/// mojibake before the backend saw them. The wire is JSON, which is UTF-8; malformed sequences are
/// replaced, as every terminal does, and the command's real exit code and the rest of its output
/// come back.
Converter<List<int>, String> commandOutputDecoder() => const Utf8Decoder(allowMalformed: true);

/// The largest file `read_file` returns (audit INT-036), the same 2 MiB the control server's
/// own `GET /read` allows. A bigger file used to be read whole and sent as one frame, which a
/// backend whose WebSocket message limit is smaller answers by closing the connection (1009),
/// failing every other call in flight with it. Refusing with a clear error keeps the connection
/// and tells the agent what to do instead.
const maxReadFileBytes = 2 * 1024 * 1024;

/// Called once per chunk of output as a running command produces it (`stream` being `"stdout"` or
/// `"stderr"`). Optional: [TaskHandler.run] behaves identically without one, just without giving
/// the caller a way to observe output before the command finishes.
typedef OutputChunkCallback = void Function(String stream, String data);

class TaskHandler {
  TaskHandler(
    this._roots, [
    ApprovalQueue? approvalQueue,
    GitService? gitService,
    IsolatedTaskRegistry? isolatedTasks,
  ])  : _approvals = approvalQueue ?? ApprovalQueue(),
        _isolatedTasks = isolatedTasks ?? IsolatedTaskRegistry(_roots, gitService),
        _git = gitService ?? GitService(),
        _refreshRoots = null,
        _remoteControlEnabled = _remoteControlOff;

  /// The daemon's own wiring (`backend_connection.dart`): resolves every RPC against
  /// [WorkspaceStore.live] -- the same instance the control server's grant/revoke/trust/untrust
  /// updates on save -- and re-reads the store at the start of each RPC, so a change made by the
  /// CLI in another process applies too (audit INT-002). [isolatedTasks] must have been built on
  /// `store.live` as well; the default one is.
  ///
  /// [remoteControlEnabled] answers whether this computer accepts remote tasks (WP8), read live
  /// on every call -- the daemon passes `ConfigStore.remoteControlEnabled`. Without one, remote
  /// control is off. [gitService] runs `list_roots`' `is_git` probe and `init_git`.
  TaskHandler.live(
    WorkspaceStore store, {
    ApprovalQueue? approvalQueue,
    IsolatedTaskRegistry? isolatedTasks,
    bool Function()? remoteControlEnabled,
    GitService? gitService,
  })  : _roots = store.live,
        _approvals = approvalQueue ?? ApprovalQueue(),
        _isolatedTasks = isolatedTasks ?? IsolatedTaskRegistry(store.live, gitService),
        _git = gitService ?? GitService(),
        _refreshRoots = store.refreshLive,
        _remoteControlEnabled = remoteControlEnabled ?? _remoteControlOff;

  /// WP4: `list_roots`' `is_git` probe and the `init_git` RPC -- the one way the agent-facing side
  /// reaches git outside [IsolatedTaskRegistry], see `git_service.dart`'s doc comment.
  final GitService _git;

  static bool _remoteControlOff() => false;

  /// Whether this computer's `remote_control` flag is on now (WP8, D-10b).
  final bool Function() _remoteControlEnabled;

  /// Task ids created with `origin: "remote"`. Never reaped, like [_activeTaskIds].
  final Set<String> _remoteTasks = {};

  final WorkspaceRoots _roots;

  /// Re-reads the grants before each RPC, when this handler was built with [TaskHandler.live].
  final void Function()? _refreshRoots;

  void _refresh() => _refreshRoots?.call();
  /// Pending approvals by the RPC call id that is waiting on them, so a `cancel` frame from the
  /// server (audit FSA-041) can resolve the right approval.
  final Map<String, String> _approvalByCall = {};

  /// D5's interactive consent queue -- see `approval_queue.dart`. Defaults to a private instance
  /// when not supplied, so every pre-D5 call site (a single positional `TaskHandler(roots)`, in
  /// tests and in `bin/aura_hostd.dart`) keeps compiling unchanged; `bin/aura_hostd.dart` passes
  /// the same queue instance `ControlServer` uses, so `GET /approvals` sees what `run` queues.
  final ApprovalQueue _approvals;

  /// D8/D9's shared per-task isolated-worktree bookkeeping (`isolated_task_registry.dart`) --
  /// shared with `ControlServer` (D9's "Agent Review" panel needs to see what this class has
  /// isolated on the agent's behalf) rather than owned privately here as it was in D8's first
  /// cut. Constructing one with a fresh `GitService` when not supplied preserves every existing
  /// `TaskHandler(roots, approvals, gitService)` call site's exact behavior.
  final IsolatedTaskRegistry _isolatedTasks;

  /// Non-isolated tasks bound to the IDE's active root at `create_task` (FSA-006); isolated
  /// tasks are bound through [_isolatedTasks] instead. Never reaped: a task_id is minted per
  /// turn and the entries are two strings each, like [_activeTaskIds].
  /// What [minimalEnvironment] filters an agent command's environment from (G-06, SEC-04). The
  /// daemon's own environment; a field so a test can plant a secret in it and check the command
  /// never sees it.
  Map<String, String> parentEnvironment = Platform.environment;

  /// Running commands by the RPC call id that started them, so a `cancel` frame stops the command
  /// itself and not only a pending approval (G-16, SEC-09).
  final Map<String, void Function()> _runningByCall = {};

  /// Every command process tree currently running, so [stopRunningCommands] can end them all.
  final Set<ProcessTree> _runningTrees = {};
  final Map<String, WorkspaceRoot> _boundRoots = {};
  final Set<String> _activeTaskIds = {};

  Future<Map<String, Object?>> createTask(Map<String, dynamic> params) async {
    _refresh();
    final remote = _parseOrigin(params['origin']);
    if (remote && !_remoteControlEnabled()) throw RpcError(remoteControlDisabledMessage, code: HostdErrorCode.remoteControlDisabled);
    final taskId = 't_${DateTime.now().microsecondsSinceEpoch}_${_activeTaskIds.length}';
    _activeTaskIds.add(taskId);

    final isolate = params['isolate'] == true;
    final rawTaskKey = params['task_key'];
    final taskKey = isolate && rawTaskKey != null ? _requireSafeTaskKey(rawTaskKey) : null;
    if (taskKey != null) {
      _isolatedTasks.registerConfig(taskId, taskKey: taskKey, baseBranch: params['base_branch'] as String?);
    }
    // FSA-006: the Desktop IDE's active workspace root, sent by the backend as `cwd`
    // (aura_backend/aura_core/adapters/host_agent.py::create_task). It binds this task to that
    // root for every RPC made on the task_id -- run, write, read, git -- so a user with several
    // granted roots gets the Engineer in the one the IDE shows, not in whichever root was granted
    // first. A path outside every granted root is refused here, before anything runs. Absent
    // (older backend, no open workspace) nothing is bound and each RPC falls back to its own
    // lookup, as before.
    final cwdParam = params['cwd'] as String?;
    if (cwdParam != null) {
      final root = _roots.findContainingRoot(cwdParam);
      if (root == null) {
        throw RpcError('no granted workspace root contains "$cwdParam" -- grant it first');
      }
      if (taskKey != null) {
        // The worktree is carved out of the root the IDE shows, not out of the first granted one.
        // G-04: a worktree that resolves outside the folder, or none at all, is an error -- not a
        // task left unbound, whose writes would then land in the live checkout.
        final WorkspaceRoot? worktree;
        try {
          worktree = await _isolatedTasks.resolve(taskId, cwdParam);
        } on ContainmentViolation catch (error) {
          throw RpcError(error.message, code: error.code);
        } on IsolationNeedsGitError catch (error) {
          // WP4 (D-13): refused before anything is created; the person is offered "Initialize
          // git here" instead.
          throw RpcError(error.message, code: error.code);
        }
        if (worktree == null) throw _noWorktreeError(taskKey);
      } else {
        _boundRoots[taskId] = root;
      }
    }
    // Recorded only once the task is fully set up, so a refused create_task leaves nothing behind.
    if (remote) _remoteTasks.add(taskId);
    return {'task_id': taskId, 'isolated': taskKey != null};
  }

  /// `list_roots` (WP8): the folders the agent may use, as the grants stand now, and whether this
  /// computer accepts remote tasks -- so the phone or web app can offer a folder picker and say
  /// when remote control is off. Needs no task, and answers for any origin: it reveals nothing
  /// the desktop app's own status does not already show the signed-in account.
  ///
  /// Each root carries `is_git` (WP4, D-13): true iff an isolated task can work there -- a git
  /// work tree with at least one commit -- so the app can offer "Initialize git here" where it is
  /// false. A probe that fails or times out reports false.
  Future<Map<String, Object?>> listRoots(Map<String, dynamic> params) async {
    _refresh();
    final roots = _roots.all.toList(growable: false);
    final isGit = await Future.wait([for (final root in roots) _isolationReady(root.canonicalPath)]);
    return {
      'roots': [
        for (var i = 0; i < roots.length; i++)
          {'path': roots[i].canonicalPath, 'trusted': roots[i].trusted, 'is_git': isGit[i]},
      ],
      'remote_control': _remoteControlEnabled(),
    };
  }

  Future<bool> _isolationReady(String path) async {
    try {
      return await _git.isolationState(path) == GitIsolationState.ready;
    } on Object {
      return false;
    }
  }

  /// `init_git` (WP4, D-13): the phone's or the web's "Initialize git here" for the granted folder
  /// `path` -- `git init`, `git add -A` and a first commit (`git_init.dart`, the same as the control
  /// server's `POST /git/init`). This RPC exists only for the phone and the web, so remote control
  /// must be on, and the person's tap must have reached here as `confirmed: true`. Returns
  /// `{"ok": true, "commit": <sha>}`.
  Future<Map<String, Object?>> initGit(Map<String, dynamic> params) async {
    _refresh();
    if (!_remoteControlEnabled()) {
      throw RpcError(remoteControlDisabledMessage, code: HostdErrorCode.remoteControlDisabled);
    }
    _requireRemoteConfirmation(params, 'init_git');
    final path = _requireString(params, 'path');
    try {
      return await initializeGrantedFolder(_roots, _git, path);
    } on GitInitRefusal catch (error) {
      throw RpcError(error.toString(), code: error.code, detail: error.detail);
    }
  }

  /// `true` for a remote task. An absent origin is a desktop task (older backends); any other
  /// value is refused rather than guessed at.
  bool _parseOrigin(Object? origin) {
    if (origin == null || origin == 'desktop') return false;
    if (origin == 'remote') return true;
    throw RpcError(
      'invalid origin ${jsonEncode(origin)}: it must be "desktop" or "remote"',
      code: HostdErrorCode.invalidOrigin,
    );
  }

  /// Whether [params]' task is a remote task -- refusing the call if it is and remote control has
  /// been switched off since (WP8, D-10b). Called first thing in every task RPC, after the grants
  /// are re-read, so the switch stops the very next call of a conversation already under way.
  bool _checkRemote(Map<String, dynamic> params) {
    final taskId = params['task_id'];
    if (taskId is! String || !_remoteTasks.contains(taskId)) return false;
    if (!_remoteControlEnabled()) throw RpcError(remoteControlDisabledMessage, code: HostdErrorCode.remoteControlDisabled);
    return true;
  }

  /// D-10c: a remote task's `run`/`write_file`/`write_files` must carry `confirmed: true` -- the
  /// backend's statement that the person approved this exact call on their phone or the web.
  /// Only a JSON `true` counts. This is the daemon's own floor: the backend should never send an
  /// unconfirmed one, and nothing here falls back to the desktop's queue, which nobody is watching.
  /// Checked after every refusal that applies anyway (containment, `.git`, denylist), so the
  /// person is never asked to approve a call this daemon would refuse regardless. (An isolated
  /// task's worktree may already have been created by then, exactly as before a desktop approval.)
  void _requireRemoteConfirmation(Map<String, dynamic> params, String method) {
    if (params['confirmed'] == true) return;
    throw RpcError(
      'confirmation_required: this computer is being controlled from the phone or the web, so '
      'every command and file change must be approved there first -- this $method call was not',
      code: HostdErrorCode.remoteConfirmationRequired,
    );
  }

  /// G-04 (SEC-03): see `isolated_task_registry.dart::isSafeTaskKey`.
  String _requireSafeTaskKey(Object rawTaskKey) {
    if (rawTaskKey is! String || !isSafeTaskKey(rawTaskKey)) {
      throw RpcError(
        'invalid task_key: it must be a single path segment of 1-128 letters, digits, ".", "_" '
        'or "-", and not "." or ".."',
        code: HostdErrorCode.invalidTaskKey,
      );
    }
    return rawTaskKey;
  }

  RpcError _noWorktreeError([String? taskKey]) => RpcError(
        'isolation was requested${taskKey == null ? '' : ' (task_key "$taskKey")'} but no '
        'granted folder could hold its worktree -- refusing rather than acting on the live checkout',
        code: HostdErrorCode.isolatedTaskUnresolved,
      );

  /// The root a task_id was bound to at `create_task` (FSA-006): an isolated task's worktree, or
  /// the IDE root a non-isolated task was created with. `null` for a task created without one,
  /// whose RPCs each resolve their own root as they always have.
  ///
  /// Re-derived from the current grants on every call (audit INT-002): a root revoked since
  /// `create_task` refuses the call, and a trust change applies at once.
  WorkspaceRoot? _boundRootFor(String? taskId) {
    if (taskId == null) return null;
    final isolated = _isolatedTasks.boundRoot(taskId);
    if (isolated != null) return isolated;
    final bound = _boundRoots[taskId];
    if (bound == null) return null;
    final current = _roots.byCanonicalPath(bound.canonicalPath);
    if (current == null) {
      throw RpcError(
        'workspace root ${bound.canonicalPath} was revoked -- grant the folder again to continue',
      );
    }
    return current;
  }

  /// [root] as the grants stand now, or null if it is no longer granted (G-05, SEC-08) -- used
  /// after an approval, so a revoke made while the human was deciding is caught. An isolated
  /// task's worktree is not itself a granted root: it stands or falls with the folder it was carved
  /// from, and takes that folder's current trust, as `IsolatedTaskRegistry.boundRoot` does.
  WorkspaceRoot? _currentGrantFor(WorkspaceRoot root) {
    final direct = _roots.byCanonicalPath(root.canonicalPath);
    if (direct != null) return direct;
    final task = _isolatedTasks.parentOf(root.canonicalPath);
    if (task == null) return null;
    final parent = _roots.byCanonicalPath(task.parentRootPath);
    return parent == null ? null : task.root.copyWith(trusted: parent.trusted);
  }

  /// D6 plan section C.3 (the write-gating retrofit): the agent-facing write RPC now goes through
  /// the same trust/approval-queue gate `run()` already had since D5 -- previously this and
  /// `edit_file` (which routes through this same RPC on the backend, see `coding.py::edit_file`)
  /// were the one place an agent could freely mutate any file in a granted-but-untrusted root.
  Future<Map<String, Object?>> writeFile(Map<String, dynamic> params, {String? callId}) async {
    _refresh();
    final remote = _checkRemote(params);
    final path = _requireString(params, 'path');
    final content = _requireString(params, 'content');
    final taskId = params['task_id'] as String?;
    var root = _boundRootFor(taskId);
    if (root == null && _isolatedTasks.needsResolution(taskId)) {
      root = await _isolatedTasks.resolve(taskId, path) ?? (throw _noWorktreeError());
    }
    root ??= _roots.findContainingRoot(path);
    if (root == null) {
      throw RpcError(
        'no granted workspace root contains "$path" -- grant it (or its parent project folder) '
        'first',
      );
    }
    final resolved = _roots.requireContained(root, path);
    final protectedWrite = protectedGitWriteRefusal(resolved);
    if (protectedWrite != null) throw RpcError(protectedWrite.reason, code: protectedWrite.code);

    // A remote task skips the trust tier and the desktop queue: approved on the remote device, or
    // refused (WP8). A desktop task ignores `confirmed`.
    if (remote) {
      _requireRemoteConfirmation(params, 'write_file');
    } else if (evaluateWrite(workspaceTrusted: root.trusted).decision ==
        CommandDecision.needsApproval) {
      await _awaitWriteApproval(
        [WriteOp(path: path, finalContent: content)],
        cwd: root.canonicalPath,
        root: root,
        callId: callId,
      );
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
  Future<Map<String, Object?>> writeFiles(Map<String, dynamic> params, {String? callId}) async {
    _refresh();
    final remote = _checkRemote(params);
    final rawOps = params['operations'];
    if (rawOps is! List || rawOps.isEmpty) {
      throw RpcError('"operations" must be a non-empty list');
    }
    final taskId = params['task_id'] as String?;
    // Resolved once, from the first operation's path, then reused for every operation below --
    // once a task_id is bound to an ephemeral root, every path in this batch must resolve only
    // against it (D8 plan Part B.2.3), never fall back to searching other granted roots.
    var taskRoot = _boundRootFor(taskId);
    if (taskRoot == null && _isolatedTasks.needsResolution(taskId)) {
      final firstPath = rawOps.isNotEmpty && rawOps.first is Map
          ? (Map<String, dynamic>.from(rawOps.first as Map)['path'] as String?)
          : null;
      taskRoot = await _isolatedTasks.resolve(taskId, firstPath) ?? (throw _noWorktreeError());
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
      final protectedWrite = protectedGitWriteRefusal(absolute);
      if (protectedWrite != null) throw RpcError(protectedWrite.reason, code: protectedWrite.code);

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
    if (remote) {
      _requireRemoteConfirmation(params, 'write_files');
    } else if (evaluateWrite(workspaceTrusted: root.trusted).decision ==
        CommandDecision.needsApproval) {
      await _awaitWriteApproval(resolved, cwd: root.canonicalPath, root: root, callId: callId);
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

  /// The root a file RPC acts in: the task's bound root, an isolated task's worktree, or the one
  /// granted root that contains [path] -- the resolution `writeFile` does inline.
  Future<WorkspaceRoot> _rootForFileOp(String? taskId, String path) async {
    var root = _boundRootFor(taskId);
    if (root == null && _isolatedTasks.needsResolution(taskId)) {
      root = await _isolatedTasks.resolve(taskId, path) ?? (throw _noWorktreeError());
    }
    root ??= _roots.findContainingRoot(path);
    if (root == null) {
      throw RpcError(
        'no granted workspace root contains "$path" -- grant it (or its parent project folder) '
        'first',
      );
    }
    return root;
  }

  /// The absolute path of the directory entry [rawPath] *names*, without following it when it is
  /// itself a symlink: the entry's parent is resolved and checked for containment, and the final
  /// component is appended as written. `requireContained` on the whole path would resolve a link
  /// to its target, so deleting or renaming a link would act on whatever it points at instead.
  String _entryPath(WorkspaceRoot root, String rawPath) {
    final candidate = p.isAbsolute(rawPath) ? rawPath : p.join(root.canonicalPath, rawPath);
    final name = p.basename(candidate);
    if (name.isEmpty || name == '.' || name == '..') {
      throw RpcError('"$rawPath" does not name a file or directory entry');
    }
    final parent = _roots.requireContained(root, p.dirname(candidate));
    return p.join(parent, name);
  }

  /// Anything at or under a `.git` directory. `protectedGitWriteRefusal` already refuses the files
  /// that become code git runs; moving or deleting entries inside git's own storage is a different
  /// harm (a repository that no longer opens) and no editing task needs it -- `git` commands are
  /// the way to change that.
  void _refuseGitInternals(String absolute, {required String verb}) {
    final inside = absolute
        .split(RegExp(r'[/\\]'))
        .map((s) => s.toLowerCase().replaceAll(RegExp(r'[. ]+$'), ''))
        .contains('.git');
    if (inside) {
      throw RpcError(
        '$verb of "$absolute" is refused: it is inside a .git directory -- use git commands to '
        'change a repository\'s own storage',
        code: HostdErrorCode.protectedGitDirFile,
      );
    }
    final protectedWrite = protectedGitWriteRefusal(absolute);
    if (protectedWrite != null) throw RpcError(protectedWrite.reason, code: protectedWrite.code);
  }

  /// What a human approved, in a form that changes when the entry does: its type, size and
  /// modification time, not following a link. Compared again after the approval so a file edited
  /// while the person was deciding is not deleted or moved on the strength of the old state.
  String _entryFingerprint(String absolute) {
    final type = FileSystemEntity.typeSync(absolute, followLinks: false);
    if (type == FileSystemEntityType.notFound) return 'absent';
    final stat = FileStat.statSync(absolute);
    return '$type:${stat.size}:${stat.modified.microsecondsSinceEpoch}';
  }

  /// Delete one file (or symlink) inside a granted root. Directories are refused: deleting a tree
  /// is the case that destroys the most for the least typing, so it stays a deliberate
  /// `run_command` that goes through the command policy.
  ///
  /// Gated exactly as `writeFile` is: refusals first, then a remote task's confirmation or an
  /// untrusted desktop root's approval queue -- the human sees the file's content as removed lines.
  Future<Map<String, Object?>> deleteFile(Map<String, dynamic> params, {String? callId}) async {
    _refresh();
    final remote = _checkRemote(params);
    final path = _requireString(params, 'path');
    final taskId = params['task_id'] as String?;
    final root = await _rootForFileOp(taskId, path);
    final entry = _entryPath(root, path);
    _refuseGitInternals(entry, verb: 'delete');

    final type = FileSystemEntity.typeSync(entry, followLinks: false);
    if (type == FileSystemEntityType.notFound) throw RpcError('"$path" does not exist');
    if (type == FileSystemEntityType.directory) {
      throw RpcError(
        '"$path" is a directory -- delete_file removes one file at a time; use run_command to '
        'remove a directory',
      );
    }

    if (remote) {
      _requireRemoteConfirmation(params, 'delete_file');
    } else if (evaluateWrite(workspaceTrusted: root.trusted).decision ==
        CommandDecision.needsApproval) {
      var preview = 'delete symbolic link $path';
      if (type == FileSystemEntityType.file) {
        final List<int> bytes;
        try {
          bytes = File(entry).readAsBytesSync();
        } on FileSystemException catch (error) {
          throw RpcError('could not read "$path" to show what would be deleted: ${error.message}');
        }
        try {
          preview = unifiedDiff(path: path, before: utf8.decode(bytes), after: '');
        } on FormatException {
          // Not text: show what is known rather than refuse to ask.
          preview = 'delete binary file $path (${bytes.length} bytes)';
        }
      }
      await _awaitEntryApproval(
        path: path,
        preview: preview,
        root: root,
        verb: 'delete',
        callId: callId,
        entries: [entry],
      );
    }

    // Re-derive from the grants as they stand now: an approval can have outlived a revoke.
    final current = _currentGrantFor(root) ?? (throw RpcError('workspace root is no longer granted'));
    final target = _entryPath(current, path);
    try {
      if (FileSystemEntity.typeSync(target, followLinks: false) == FileSystemEntityType.link) {
        Link(target).deleteSync();
      } else {
        File(target).deleteSync();
      }
    } on FileSystemException catch (error) {
      throw RpcError('could not delete "$path": ${error.message}');
    }
    return {'deleted': path};
  }

  /// Rename (move) one entry inside a granted root. Both ends must lie in the same root; the
  /// destination must not exist -- a rename never silently replaces a file, because that would be
  /// a delete the person did not see.
  Future<Map<String, Object?>> renameFile(Map<String, dynamic> params, {String? callId}) async {
    _refresh();
    final remote = _checkRemote(params);
    final path = _requireString(params, 'path');
    final newPath = _requireString(params, 'new_path');
    final taskId = params['task_id'] as String?;
    final root = await _rootForFileOp(taskId, path);
    final source = _entryPath(root, path);
    final destination = _entryPath(root, newPath);
    _refuseGitInternals(source, verb: 'rename');
    _refuseGitInternals(destination, verb: 'rename');

    final sourceType = FileSystemEntity.typeSync(source, followLinks: false);
    if (sourceType == FileSystemEntityType.notFound) throw RpcError('"$path" does not exist');
    if (FileSystemEntity.typeSync(destination, followLinks: false) != FileSystemEntityType.notFound) {
      throw RpcError('"$newPath" already exists -- rename never replaces an existing entry');
    }
    if (sourceType == FileSystemEntityType.directory && p.isWithin(source, destination)) {
      throw RpcError('cannot move "$path" into itself ("$newPath")');
    }

    if (remote) {
      _requireRemoteConfirmation(params, 'rename_file');
    } else if (evaluateWrite(workspaceTrusted: root.trusted).decision ==
        CommandDecision.needsApproval) {
      await _awaitEntryApproval(
        path: '$path -> $newPath',
        preview: 'rename $path -> $newPath',
        root: root,
        verb: 'rename',
        callId: callId,
        entries: [source, destination],
      );
    }

    final current = _currentGrantFor(root) ?? (throw RpcError('workspace root is no longer granted'));
    final from = _entryPath(current, path);
    final to = _entryPath(current, newPath);
    try {
      Directory(p.dirname(to)).createSync(recursive: true);
      switch (sourceType) {
        case FileSystemEntityType.directory:
          Directory(from).renameSync(to);
        case FileSystemEntityType.link:
          Link(from).renameSync(to);
        default:
          File(from).renameSync(to);
      }
    } on FileSystemException catch (error) {
      throw RpcError('could not rename "$path" to "$newPath": ${error.message}');
    }
    return {'renamed': path, 'to': newPath};
  }

  /// Approval for a delete or rename: queue it with [preview] as what the person reviews, wait for
  /// the answer, then check the grants and every entry in [entries] are still as they were when the
  /// preview was made (type, size, modification time -- and a destination that was absent is still
  /// absent).
  Future<void> _awaitEntryApproval({
    required String path,
    required String preview,
    required WorkspaceRoot root,
    required String verb,
    required List<String> entries,
    String? callId,
  }) async {
    final before = {for (final e in entries) e: _entryFingerprint(e)};
    final PendingApproval approval;
    try {
      approval = _approvals.createForWrite(
        operations: [WriteOp(path: path, finalContent: '')],
        diffText: preview,
        cwd: root.canonicalPath,
        rootPath: root.canonicalPath,
      );
    } on ApprovalQueueFullError catch (error) {
      throw RpcError(error.message, code: error.code);
    }
    await _awaitDecision(approval, callId: callId, verb: verb, subject: path);

    _refresh();
    if (_currentGrantFor(root) == null) {
      throw RpcError('workspace root is no longer granted -- refusing approved $verb: $path');
    }
    for (final entry in before.entries) {
      if (_entryFingerprint(entry.key) != entry.value) {
        throw RpcError(
          '"$path" changed on disk since this $verb was approved -- refusing, ask again to '
          'review the current state',
        );
      }
    }
  }

  Future<Map<String, Object?>> readFile(Map<String, dynamic> params) async {
    _refresh();
    // Read-only: no confirmation for a remote task, but it still stops once remote control is off.
    _checkRemote(params);
    final path = _requireString(params, 'path');
    final taskId = params['task_id'] as String?;
    var root = _boundRootFor(taskId);
    if (root == null && _isolatedTasks.needsResolution(taskId)) {
      root = await _isolatedTasks.resolve(taskId, path) ?? (throw _noWorktreeError());
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
    final size = file.lengthSync();
    if (size > maxReadFileBytes) {
      throw RpcError(
        '$path is $size bytes, over the $maxReadFileBytes-byte read_file limit -- read a smaller '
        'file, or use run_command (e.g. head, grep or sed -n) to read part of it',
      );
    }
    return {'content': file.readAsStringSync()};
  }

  Future<Map<String, Object?>> run(
    Map<String, dynamic> params, {
    OutputChunkCallback? onChunk,
    String? callId,
  }) async {
    // The caller (`aura_backend/aura_core/tools/coding.py::run_command`) always sends
    // `["sh", "-c", "<shell command>"]` -- a full argv, not a bare string -- so it can be
    // `Process.start`ed directly without this daemon re-interpreting it through another shell.
    // Tolerate other argv shapes too (a future caller might not go through `sh -c`) by falling
    // back to running the argv's first element as the program.
    _refresh();
    final remote = _checkRemote(params);
    final commandArgv = _requireStringList(params, 'command');
    if (commandArgv.isEmpty) {
      throw RpcError('"command" must be a non-empty list of arguments');
    }
    // A per-call `cwd` is tolerated for callers that do not bind their task at `create_task`
    // (FSA-006): resolved and contained exactly like a path the agent named itself. For a bound
    // task -- the backend since the FSA-006 follow-up -- the binding is authoritative and a
    // per-call `cwd` is ignored: an isolated task's worktree lies outside the parent root the IDE
    // names, and honouring the parent would either escape the worktree or refuse the command.
    final cwdParam = params['cwd'] as String?;
    final requestedTimeout = (params['timeout_s'] as num?)?.toDouble() ?? 60.0;
    final taskId = params['task_id'] as String?;

    var root = _boundRootFor(taskId);
    final bound = root != null;
    if (root == null && _isolatedTasks.needsResolution(taskId)) {
      root = await _isolatedTasks.resolve(taskId, cwdParam) ?? (throw _noWorktreeError());
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
    final cwd = cwdParam != null && !bound
        ? _roots.requireContained(root, cwdParam)
        : root.canonicalPath;

    String? approvalNote;
    final commandForPolicy = _effectiveShellCommand(commandArgv);
    // WP8: a remote task gets neither the trusted tier nor the read-only allowlist -- only the
    // every-workspace refusals, then the remote device's approval (`confirmed`), never the queue.
    final verdict = remote
        ? evaluateRemoteCommand(commandForPolicy)
        : evaluateCommand(
            commandForPolicy,
            workspaceTrusted: root.trusted,
            workspaceRoot: root.canonicalPath,
            cwd: cwd,
          );
    if (verdict.decision == CommandDecision.deny) {
      throw RpcError('command refused: ${verdict.reason}', code: verdict.code);
    }
    if (remote) {
      _requireRemoteConfirmation(params, 'run');
    } else if (verdict.decision == CommandDecision.needsApproval) {
      final waited = await _awaitApproval(
        commandForPolicy, cwd: cwd, root: root, callId: callId, reason: verdict.reason,
      );
      // The agent sees nothing while a human decides; tell it afterwards that it was held, why, and
      // that the answer was yes (a no or a timeout is an error that says the same).
      approvalNote = '[aura_hostd] this command was held for your approval (${verdict.reason}) '
          'and approved after ${waited.inSeconds}s';
    }
    // G-17 (SEC-10): the untrusted tier's allowlisted git runs with the repository's own
    // fsmonitor, hooks, external diff and textconv programs switched off. An approved command
    // (on the desktop, or a remote task's confirmed one) is left as the human saw it.
    final argvToRun = !remote && verdict.isAllowed && !root.trusted
        ? _hardenUntrustedGit(commandArgv, commandForPolicy)
        : commandArgv;

    final timeout = Duration(
      milliseconds: (requestedTimeout * 1000).clamp(1000, _maxCommandTimeout.inMilliseconds).round(),
    );

    final effectiveArgv = _resolveShellArgv(_lineBuffered(argvToRun));
    final ProcessTree tree;
    try {
      // G-06 (SEC-04): a minimal allowlisted environment, never the daemon's own -- see
      // `process_env.dart`. G-16 (SEC-09): started so a timeout or cancel can stop everything it
      // spawns -- see `process_tree.dart`.
      tree = await ProcessTree.start(
        effectiveArgv,
        workingDirectory: cwd,
        environment: minimalEnvironment(parentEnvironment),
      );
    } on ProcessException catch (exc) {
      // A bare ProcessException here gave no way to tell "the resolved shell path was wrong" from
      // "the working directory was wrong" from anything else without reproducing it live outside
      // the running daemon -- confirmed painful once already. Surfacing what was actually
      // attempted, and whether each half of it existed at the moment of failure, turns the next
      // occurrence into something diagnosable from the error text alone.
      throw RpcError(
        'failed to start ${effectiveArgv.first} in $cwd: $exc '
        '(executable exists: ${File(effectiveArgv.first).existsSync()}, '
        'cwd exists: ${Directory(cwd).existsSync()})',
      );
    }

    final process = tree.process;
    _runningTrees.add(tree);
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

    // UTF-8 on every platform, never `SystemEncoding`: that is the ANSI code page on Windows, which
    // turned "café" and Arabic into mojibake before the backend ever saw them, and on POSIX it is a
    // strict decoder, so one stray byte (`cat` on a latin-1 file) threw a FormatException out of the
    // stream and failed the whole call. The wire is JSON, which is UTF-8; bytes that are not valid
    // UTF-8 become U+FFFD, the same way the backend decodes a container's output.
    final stdoutDone = process.stdout
        .transform(commandOutputDecoder())
        .forEach((chunk) => capture('stdout', stdoutBuf, chunk));
    final stderrDone = process.stderr
        .transform(commandOutputDecoder())
        .forEach((chunk) => capture('stderr', stderrBuf, chunk));

    int exitCode;
    var timedOut = false;
    var cancelled = false;
    // Completed by the timeout or by `cancel`; either way the whole tree is killed (G-16).
    final stop = Completer<void>();
    final timer = Timer(timeout, () {
      timedOut = true;
      if (!stop.isCompleted) stop.complete();
    });
    if (callId != null) {
      _runningByCall[callId] = () {
        cancelled = true;
        if (!stop.isCompleted) stop.complete();
      };
    }
    try {
      exitCode = await Future.any<int>([
        process.exitCode,
        stop.future.then((_) async {
          await tree.kill();
          return -1;
        }),
      ]);
      // The killed process's own exit can win the race above; a stopped command reports -1 either
      // way, as it always has on timeout.
      if (timedOut || cancelled) exitCode = -1;
    } finally {
      _runningTrees.remove(tree);
      timer.cancel();
      if (callId != null) _runningByCall.remove(callId);
      // A command that exits normally can still leave a background child holding the
      // stdout/stderr pipe fds open (`sh -c 'server &'`). Waiting on stdoutDone/stderrDone
      // unboundedly would then hang this whole call until that child exits on its own (observed: a
      // `sleep 30` held the pipes for the full 30 seconds). Bounding this wait is the fix: a
      // normally terminating process's streams close well within this window, and a still-running
      // child does not get to stall the RPC response it's no longer even attached to. (On a
      // timeout or cancel the whole process group is killed above, so nothing is left to wait on.)
      await Future.wait([stdoutDone, stderrDone]).timeout(
        const Duration(seconds: 2),
        onTimeout: () => const <void>[],
      );
    }

    if (timedOut) {
      stderrBuf.write('\n[aura_hostd] command killed after ${timeout.inSeconds}s timeout');
    } else if (cancelled) {
      stderrBuf.write('\n[aura_hostd] command cancelled by the server and killed');
    }
    if (outputTruncated) {
      stderrBuf.write('\n[aura_hostd] output truncated after $_maxOutputCapChars characters');
    }
    if (approvalNote != null) stderrBuf.write('\n$approvalNote');

    return {
      'stdout': stdoutBuf.toString(),
      'stderr': nameMissingExecutable(stderrBuf.toString(), exitCode, commandArgv),
      'exit_code': exitCode,
    };
  }

  /// The server gave up on [callId] (its tool timed out or the turn was cancelled). If the call
  /// is waiting for a human, the approval is cancelled and the call throws instead of executing
  /// (audit FSA-041); if it is a running command, the command and everything it started are killed
  /// and the call returns what it had produced (G-16, SEC-09). A call that already finished is
  /// unaffected.
  void cancel(String callId) {
    final approvalId = _approvalByCall.remove(callId);
    if (approvalId != null) _approvals.cancel(approvalId);
    _runningByCall.remove(callId)?.call();
  }

  /// The connection these calls arrived on is gone (audit INT-007). The backend failed every
  /// one of them the moment it saw the drop (`HostAgentSession.fail_all_pending`), so nothing
  /// still waiting for a human may run afterwards: an approval granted now would execute a
  /// command or write whose result nobody receives, and a model retry could run it twice.
  /// Cancels every approval a call is waiting on; a call already running is unaffected.
  /// The daemon is stopping (SIGTERM/SIGINT, `POST /shutdown`, a re-pair that replaces this
  /// handler's connection): kill every command this handler started that is still running, with
  /// everything it spawned. Found by driving the real daemon: stopping it left the agent's
  /// commands -- a dev server, a long build -- running as the user with nothing left to read their
  /// output or stop them. A dropped *socket* deliberately does not do this (see [cancelAll]); only
  /// the daemon going away does.
  Future<void> stopRunningCommands() async {
    await Future.wait([for (final tree in _runningTrees.toList()) tree.kill()]);
  }

  void cancelAll() {
    final approvalIds = _approvalByCall.values.toList();
    _approvalByCall.clear();
    for (final approvalId in approvalIds) {
      _approvals.cancel(approvalId);
    }
  }

  void _trackApproval(String? callId, PendingApproval approval) {
    if (callId != null) _approvalByCall[callId] = approval.id;
  }

  void _untrackApproval(String? callId) {
    if (callId != null) _approvalByCall.remove(callId);
  }

  /// Queues [command] for interactive approval and blocks until a human answers or it times out
  /// (`approval_queue.dart`'s [approvalTimeout]). On approval, re-derives the command's identity
  /// hash from the *current* call context and compares it against the hash the approval was
  /// bound to at request time -- if the root this [cwd] resolves under has changed (e.g. revoked)
  /// since the request was queued, the hashes diverge and this refuses to run rather than
  /// executing something the human no longer sees the same context for (D5 plan design decision
  /// 5). Throws [RpcError] on rejection, expiry, or a revalidation mismatch; returns normally only
  /// when the exact operation that was approved is still safe to run.
  ///
  /// [reason] is why the command was held (the policy's own words); it goes into the messages so the
  /// agent, which sees nothing while a human decides, learns afterwards *why* it waited and what
  /// came of it. Returns how long the human took.
  Future<Duration> _awaitApproval(
    String command, {
    required String cwd,
    required WorkspaceRoot root,
    String? callId,
    String? reason,
  }) async {
    final waited = Stopwatch()..start();
    final why = reason == null || reason.isEmpty ? '' : ' (held for approval because: $reason)';
    final PendingApproval approval;
    try {
      approval = _approvals.createForCommand(canonicalCommand: command, cwd: cwd, rootPath: root.canonicalPath);
    } on ApprovalQueueFullError catch (error) {
      throw RpcError(error.message, code: error.code); // G-21
    }
    _trackApproval(callId, approval);
    final ApprovalStatus status;
    try {
      status = await _approvals.awaitDecision(approval);
    } finally {
      _untrackApproval(callId);
    }
    switch (status) {
      case ApprovalStatus.approved:
        break;
      case ApprovalStatus.rejected:
        throw RpcError(
            'command was rejected by the user after ${waited.elapsed.inSeconds}s$why: $command');
      case ApprovalStatus.expired:
        throw RpcError(
            'command approval timed out: the user did not answer on their computer within '
            '${waited.elapsed.inSeconds}s$why, so it was not run: $command');
      case ApprovalStatus.cancelled:
        throw RpcError('command approval was cancelled$why: $command');
      case ApprovalStatus.pending:
        throw RpcError('command approval resolved with no decision -- this should not happen');
    }
    // G-05: re-read the grants, then re-derive *this* root from them -- for an isolated task its
    // worktree from the folder it was carved from, not whichever granted folder contains [cwd]
    // (that is the parent, whose path would never match the approval's).
    _refresh();
    final currentRoot = _currentGrantFor(root);
    final currentHash = currentRoot == null
        ? null
        : PendingApproval.hashForCommand(command: command, cwd: cwd, rootPath: currentRoot.canonicalPath);
    if (currentHash != approval.operationHash) {
      throw RpcError(
        'command context changed since it was approved (workspace root no longer matches) -- '
        'refusing to run: $command',
      );
    }
    return waited.elapsed;
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
    String? callId,
  }) async {
    final beforeSnapshots = <String, String>{};
    final diffParts = <String>[];
    for (final op in operations) {
      final absolute = _roots.requireContained(root, op.path);
      final before = File(absolute).existsSync() ? File(absolute).readAsStringSync() : '';
      beforeSnapshots[op.path] = before;
      diffParts.add(unifiedDiff(path: op.path, before: before, after: op.finalContent));
    }

    final PendingApproval approval;
    try {
      approval = _approvals.createForWrite(
        operations: operations,
        diffText: diffParts.join('\n'),
        cwd: cwd,
        rootPath: root.canonicalPath,
      );
    } on ApprovalQueueFullError catch (error) {
      throw RpcError(error.message, code: error.code); // G-21
    }
    final pathList = operations.map((o) => o.path).join(', ');
    await _awaitDecision(approval, callId: callId, verb: 'write', subject: pathList);

    // G-05: re-read the grants, then re-derive *this* root from them (an isolated worktree, not
    // the folder it sits in, so relative paths are re-read where they will be written).
    _refresh();
    final currentRoot = _currentGrantFor(root);
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

  /// Blocks until a human answers [approval], then throws unless they approved it. [verb] and
  /// [subject] only shape the message ("write was rejected by the user: a.txt").
  Future<void> _awaitDecision(
    PendingApproval approval, {
    required String? callId,
    required String verb,
    required String subject,
  }) async {
    _trackApproval(callId, approval);
    final ApprovalStatus status;
    try {
      status = await _approvals.awaitDecision(approval);
    } finally {
      _untrackApproval(callId);
    }
    switch (status) {
      case ApprovalStatus.approved:
        return;
      case ApprovalStatus.rejected:
        throw RpcError('$verb was rejected by the user: $subject');
      case ApprovalStatus.expired:
        throw RpcError('$verb approval timed out waiting for a response: $subject');
      case ApprovalStatus.cancelled:
        throw RpcError('$verb approval was cancelled: $subject');
      case ApprovalStatus.pending:
        throw RpcError('$verb approval resolved with no decision -- this should not happen');
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
      throw RpcError('missing or invalid "$key" parameter', code: HostdErrorCode.missingParameter);
    }
    return value;
  }

  List<String> _requireStringList(Map<String, dynamic> params, String key) {
    final value = params[key];
    if (value is! List) {
      throw RpcError('missing or invalid "$key" parameter', code: HostdErrorCode.missingParameter);
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

/// [commandArgv] with [hardenUntrustedGitCommand] applied (G-17): the shell-wrapper shape the
/// backend sends has its command string rewritten; a bare `["git", <verb>, ...]` argv gets the same
/// options inserted as separate arguments. Anything else is returned unchanged.
List<String> _hardenUntrustedGit(List<String> commandArgv, String commandForPolicy) {
  final hooks = sharedEmptyHooksDir();
  if (commandArgv.length == 3 && commandArgv[2] == commandForPolicy) {
    final hardened = hardenUntrustedGitCommand(commandForPolicy, emptyHooksDir: hooks);
    return hardened == commandForPolicy ? commandArgv : [commandArgv[0], commandArgv[1], hardened];
  }
  if (commandArgv.length >= 2 && commandArgv.first == 'git') {
    return [
      'git',
      ...untrustedGitGlobalArgs(hooks),
      commandArgv[1],
      ...untrustedGitVerbFlags(commandArgv[1]),
      ...commandArgv.sublist(2),
    ];
  }
  return commandArgv;
}


/// Make a missing executable name itself, whatever `stdbuf` said.
///
/// `_lineBuffered` wraps every command in `stdbuf -oL -eL` where it exists. When the wrapped
/// program cannot be executed, GNU coreutils reports
/// `stdbuf: failed to run command 'prog': No such file or directory` -- the name is there -- but
/// uutils coreutils (the `stdbuf` on `ubuntu-latest` since Ubuntu 26.04) reports
/// `stdbuf: failed to execute process: No such file or directory`, which names nothing. The
/// contract the Engineer agent and the user rely on is "a missing executable tells you which one"
/// (`task_handler_test.dart`), so when the wrapper's own exec failure comes back without the
/// program's name, this appends it. Anything else -- a command that ran, a shell's own `not
/// found`, a wrapper message that already names the program -- is returned untouched.
String nameMissingExecutable(String stderr, int exitCode, List<String> commandArgv) {
  if (exitCode != 126 && exitCode != 127) return stderr;
  if (!stderr.startsWith('stdbuf:')) return stderr;
  if (commandArgv.isEmpty) return stderr;
  final program = commandArgv.first;
  if (stderr.contains(program)) return stderr;
  final separator = stderr.isEmpty || stderr.endsWith('\n') ? '' : '\n';
  return "$stderr$separator[aura_hostd] could not execute '$program': not found on PATH or not "
      'executable\n';
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

String? _windowsShellPathCache;

/// The caller (`aura_backend/aura_core/tools/coding.py::run_command` and friends) always sends
/// `["sh", "-c", "<command>"]` -- correct unconditionally for the Docker sandbox (always Linux),
/// but on Windows there is no `sh` on `PATH` by default (confirmed: only `Git\cmd` is on `PATH`,
/// not `Git\bin`/`Git\usr\bin`, where Git for Windows' bundled POSIX `sh.exe` actually lives), so
/// `Process.start('sh', ...)` fails to spawn at all -- every `run_command` call against a Windows
/// desktop fails identically regardless of workspace or command, which is exactly what surfaced as
/// a bare `SandboxError` in the Desktop IDE with no further detail. The command's own contents
/// (`cat`, `ls`, `grep`, ...) are POSIX tools too (`command_policy.dart`'s allowlist), so the fix is
/// to find Git for Windows' real `sh.exe` and substitute it -- not to reinterpret the command
/// through `cmd.exe`, which has none of those built-ins.
String? _findWindowsShell() {
  final cached = _windowsShellPathCache;
  if (cached != null) return cached.isEmpty ? null : cached;

  try {
    final where = Process.runSync('where', ['sh']);
    if (where.exitCode == 0) {
      final first = (where.stdout as String).split(RegExp(r'\r?\n')).firstWhere(
            (line) => line.trim().isNotEmpty,
            orElse: () => '',
          );
      final trimmed = first.trim();
      // `where`'s exit code and stdout only prove *some* PATH entry matched the name -- not that
      // the file is actually there right now (a stale PATH entry, a since-removed install, or a
      // path Process.start can't launch directly all report success here). Trusting that
      // unconditionally turned a real "no shell" case into a raw ProcessException instead of the
      // friendly RpcError below telling the user to install Git for Windows -- verify it the same
      // way the fallback candidates already are before caching it.
      if (trimmed.isNotEmpty && File(trimmed).existsSync()) {
        return _windowsShellPathCache = trimmed;
      }
    }
  } catch (_) {
    // `where` itself missing or erroring -- fall through to the well-known install locations.
  }

  final candidates = [
    Platform.environment['ProgramFiles'],
    Platform.environment['ProgramFiles(x86)'],
    Platform.environment['LOCALAPPDATA'] != null
        ? '${Platform.environment['LOCALAPPDATA']}\\Programs'
        : null,
  ].whereType<String>().expand((base) => [
        '$base\\Git\\bin\\sh.exe',
        '$base\\Git\\usr\\bin\\sh.exe',
      ]);

  for (final candidate in candidates) {
    if (File(candidate).existsSync()) {
      return _windowsShellPathCache = candidate;
    }
  }

  _windowsShellPathCache = ''; // cache the miss too -- don't re-probe the filesystem every call
  return null;
}

/// Substitutes a real shell executable for the bare `sh`/`bash` name on Windows; a no-op
/// everywhere else, where normal `PATH` resolution already finds the real thing.
List<String> _resolveShellArgv(List<String> argv) {
  if (!Platform.isWindows || argv.isEmpty) return argv;
  if (argv.first != 'sh' && argv.first != 'bash') return argv;
  final resolved = _findWindowsShell();
  if (resolved == null) {
    throw RpcError(
      'no POSIX shell found on this Windows machine -- install Git for Windows '
      '(https://git-scm.com/download/win) so run_command has something to execute '
      'the POSIX commands (cat, ls, grep, ...) Engineer generates against',
    );
  }
  return [resolved, ...argv.sublist(1)];
}
