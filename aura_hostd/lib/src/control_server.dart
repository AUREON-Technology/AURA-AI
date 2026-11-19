/// A loopback-only local control server so a UI running as a *separate process* on the same
/// machine (Unit 8, `aura_client`'s desktop Engineer workspace screen) can supervise this daemon
/// and browse granted workspaces -- without that UI needing to be Dart code linked into this
/// package (deliberately not a dependency of `aura_client`, see this package's own pubspec doc
/// comment) or needing to shell out to the CLI and scrape stdout for every query.
///
/// Binds `127.0.0.1` on an ephemeral port and writes `{port, token}` to `control.json` in the
/// daemon's config dir so a same-machine process can discover it; the token (a random, per-run
/// secret) is required on every request except a reduced `GET /status` (audit INT-037: without
/// the token it says only whether the daemon is paired and connected -- never the granted root
/// paths or the backend URL), and is the only thing standing
/// between "any local process" and "this daemon's granted filesystem access" -- a real but
/// disclosed limitation for v1 (a local process with filesystem access to the config dir can also
/// read the token file; this is a convenience boundary against accidental cross-talk, not a
/// defense against a compromised machine, which this whole daemon's threat model already excludes
/// -- ADR-011's containment story is about a compromised *backend*, not a compromised local OS).
///
/// Every filesystem-touching handler goes through [WorkspaceRoots.requireContained] exactly like
/// [TaskHandler] does -- this server has no separate path-handling logic that could bypass it.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:path/path.dart' as p;

import 'approval_queue.dart';
import 'config.dart';
import 'containment.dart';
import 'debug_session_manager.dart';
import 'diagnostics_service.dart';
import 'error_codes.dart';
import 'git_init.dart';
import 'git_service.dart';
import 'isolated_task_registry.dart';
import 'pairing_client.dart';
import 'terminal_session_manager.dart';
import 'workspace_store.dart';

/// Bounds how much of a file's content this server ever reads into memory to serve `GET /read`
/// -- a file browser previewing a huge binary or log file should not be able to grow this
/// process's memory unboundedly, matching the same discipline `task_handler.dart` applies to
/// command output.
const _maxReadBytes = 2 * 1024 * 1024;

class ControlServer {
  ControlServer({
    required this.workspaceStore,
    required this.configStore,
    required this.onPaired,
    required this.terminalSessions,
    required this.debugSessions,
    required this.approvalQueue,
    required this.isolatedTasks,
    bool Function()? isBackendConnected,
    bool Function()? isCredentialsRejected,
    int? Function()? lastCloseCode,
    Duration minPairingPollInterval = const Duration(seconds: 1),
    GitService? gitService,
    DiagnosticsService? diagnosticsService,
    ControlServerLogger? logger,
  })  : isBackendConnected = isBackendConnected ?? (() => false),
        isCredentialsRejected = isCredentialsRejected ?? (() => false),
        lastCloseCode = lastCloseCode ?? (() => null),
        _minPairingPollInterval = minPairingPollInterval,
        // F45: the repository's own hooks and fsmonitor run only in a folder the person trusts.
        gitService = gitService ??
            GitService(
              runsRepositoryPrograms: (cwd) =>
                  workspaceStore.load().findContainingRoot(cwd)?.trusted ?? false,
            ),
        diagnosticsService = diagnosticsService ?? DiagnosticsService(),
        _logger = logger ?? _defaultLogger;

  final WorkspaceStore workspaceStore;
  final ConfigStore configStore;
  final TerminalSessionManager terminalSessions;

  /// D7 Part A.3's Debug Console -- see `debug_session_manager.dart`'s doc comment for why this
  /// mirrors [terminalSessions]'s shape exactly.
  final DebugSessionManager debugSessions;

  /// D5's interactive consent queue -- the same instance `TaskHandler` queues commands into, so
  /// `GET /approvals` and `POST /approvals/<id>/respond` here act on exactly what `run` is
  /// waiting on. See `approval_queue.dart`.
  final ApprovalQueue approvalQueue;

  /// D5's direct-loopback git execution for the Source Control panel -- see `git_service.dart`'s
  /// doc comment for why this is a hard human-only boundary, never reachable via `TaskHandler`.
  final GitService gitService;

  /// D7 Part A.2's process-based batch linting -- see `diagnostics_service.dart`'s doc comment
  /// for why this is a hard human-only boundary like git, never reachable via `TaskHandler`.
  final DiagnosticsService diagnosticsService;

  /// D8/D9's shared per-task isolated-worktree bookkeeping -- **must** be the exact same instance
  /// `TaskHandler` was constructed with (`bin/aura_hostd.dart` wires this), or D9's "Agent
  /// Review" panel would see none of what the agent has actually isolated. See
  /// `isolated_task_registry.dart`'s own doc comment.
  final IsolatedTaskRegistry isolatedTasks;

  /// Called once a `GET /pair/status` poll finds the pairing approved -- the caller (`bin/
  /// aura_hostd.dart`) uses this to start the backend WebSocket connection if it wasn't already
  /// running (the daemon can be started with `run` before any token exists, precisely so this
  /// control server -- and thus a UI driving it -- is reachable to pair through).
  final void Function(String accessToken) onPaired;

  /// Whether the daemon's outbound socket to the backend (`HostAgentClient`, `rpc_client.dart`)
  /// is currently open -- distinct from `configStore`'s stored token, which only says this daemon
  /// once completed pairing, not that it is live right now. Defaults to always-`false` when the
  /// caller doesn't wire a real one (tests, or a daemon that never starts a backend connection at
  /// all), which is the honest answer in both cases.
  final bool Function() isBackendConnected;

  /// Whether the backend refused this daemon's stored token (WebSocket close 4401) and the
  /// daemon stopped reconnecting (audit INT-003). `/status` reports it as `credentials_rejected`
  /// so the UI can say "re-pair required" and offer pairing again, instead of "reconnecting..."
  /// forever while `authenticated` (a token is stored) stays true.
  final bool Function() isCredentialsRejected;

  /// The close code of the daemon's most recent backend connection, reported as
  /// `last_close_code` -- `null` before any connection has closed.
  final int? Function() lastCloseCode;

  /// The floor under the backend's advised poll interval for the daemon's own background
  /// pairing poll (INT-038). Injected so a test need not wait a second per poll.
  final Duration _minPairingPollInterval;

  final ControlServerLogger _logger;

  HttpServer? _server;
  String? _token;
  File? _controlFile;

  /// The one in-flight pairing session, if any -- only one pairing attempt makes sense at a time
  /// for a single local daemon with a single local UI driving it, so there is no session id to
  /// juggle: `/pair/start` replaces whatever was here, `/pair/status` always means "this one."
  PairingSession? _pairingSession;
  String? _pairingBackendUrl;

  /// How the most recent pairing session ended (`approved`/`expired` body), kept after the
  /// session is cleared so a UI that polls `/pair/status` only after the daemon's own background
  /// poll collected the token still learns it was approved (INT-038).
  Map<String, Object?>? _pairingOutcome;

  /// The one backend poll in flight, shared by the background poller and `/pair/status`, so the
  /// two never race to consume the same one-shot approval.
  Future<Map<String, Object?>?>? _pollInFlight;
  Timer? _pairingTimer;

  int? get port => _server?.port;

  Future<void> start(Directory configDir) async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server = server;
    _token = _randomToken();
    _controlFile = File(p.join(configDir.path, 'control.json'));
    _controlFile!.parent.createSync(recursive: true);
    writePrivateFile(_controlFile!, jsonEncode({'port': server.port, 'token': _token}));
    _logger('control server listening on 127.0.0.1:${server.port}');
    server.listen(_handle, onError: (Object e) => _logger('control server error: $e'));
  }

  Future<void> stop() async {
    _pairingTimer?.cancel();
    _pairingTimer = null;
    try {
      _controlFile?.deleteSync();
    } on FileSystemException {
      // Already gone -- fine.
    }
    await _server?.close(force: true);
  }

  Future<void> _handle(HttpRequest request) async {
    try {
      // DNS rebinding (INT-037): a web page whose hostname re-resolves to 127.0.0.1 can reach
      // this port from the user's browser, but its requests still carry that page's own Host.
      if (!_loopbackHost(request)) {
        await _fail(request, 403, HostdErrorCode.hostNotLoopback,
            'requests must address the loopback interface');
        return;
      }
      if (request.uri.path != '/status' && !_authorized(request)) {
        await _fail(request, 401, HostdErrorCode.unauthorized, 'missing or invalid control token');
        return;
      }
      final segments = request.uri.pathSegments;
      if (request.method == 'POST' &&
          segments.length == 3 &&
          segments[0] == 'approvals' &&
          segments[2] == 'respond') {
        await _handleApprovalRespond(request, segments[1]);
        return;
      }
      switch ('${request.method} ${request.uri.path}') {
        case 'GET /status':
          await _handleStatus(request);
        case 'GET /roots':
          await _handleListRoots(request);
        case 'POST /roots':
          await _handleRootAction(request);
        case 'POST /remote_control':
          await _handleRemoteControl(request);
        case 'GET /list':
          await _handleList(request);
        case 'GET /read':
          await _handleRead(request);
        case 'GET /terminal':
          await _handleTerminalUpgrade(request);
        case 'GET /debug':
          await _handleDebugUpgrade(request);
        case 'GET /approvals':
          await _handleApprovalsList(request);
        case 'GET /git/status':
          await _handleGitStatus(request);
        case 'POST /git/init':
          await _handleGitInit(request);
        case 'GET /git/diff':
          await _handleGitDiff(request);
        case 'GET /git/log':
          await _handleGitLog(request);
        case 'POST /git/stage':
          await _handleGitStage(request, unstage: false);
        case 'POST /git/unstage':
          await _handleGitStage(request, unstage: true);
        case 'POST /git/commit':
          await _handleGitCommit(request);
        case 'POST /git/checkout':
          await _handleGitCheckout(request);
        case 'GET /git/branches':
          await _handleGitBranches(request);
        case 'POST /git/fetch':
          await _handleGitFetch(request);
        case 'POST /git/pull':
          await _handleGitPull(request);
        case 'POST /git/push':
          await _handleGitPush(request);
        case 'GET /git/remotes':
          await _handleGitRemotes(request);
        case 'POST /git/publish':
          await _handleGitPublish(request);
        case 'GET /diagnostics/detect':
          await _handleDiagnosticsDetect(request);
        case 'POST /diagnostics/run':
          await _handleDiagnosticsRun(request);
        case 'GET /tasks':
          await _handleTasksList(request);
        case 'GET /git/task-diff':
          await _handleGitTaskDiff(request);
        case 'GET /git/task-diff-stat':
          await _handleGitTaskDiffStat(request);
        case 'POST /git/merge':
          await _handleGitMerge(request);
        case 'POST /git/merge-abort':
          await _handleGitMergeAbort(request);
        case 'POST /git/merge-task':
          await _handleGitMergeTask(request);
        case 'POST /git/discard-task':
          await _handleGitDiscardTask(request);
        case 'POST /pair/start':
          await _handlePairStart(request);
        case 'GET /pair/status':
          await _handlePairStatus(request);
        case 'POST /shutdown':
          await _respond(request, 200, {'status': 'shutting down'});
          unawaited(Future(() => exit(0)));
        default:
          await _fail(request, 404, HostdErrorCode.noSuchEndpoint, 'no such endpoint');
      }
    } catch (error) {
      _logger('control server: unhandled error: $error');
      try {
        await _fail(request, 500, HostdErrorCode.internalError, error.toString());
      } catch (_) {
        // Response already closed -- nothing more to do.
      }
    }
  }

  bool _authorized(HttpRequest request) {
    final header = request.headers.value(HttpHeaders.authorizationHeader);
    final token = _token;
    if (header == null || token == null) return false;
    return constantTimeEquals(header, 'Bearer $token');
  }

  /// Whether the request's Host header names this machine's loopback interface. Every
  /// legitimate client (the desktop app, the CLI, a test) connects to `127.0.0.1:<port>` and
  /// sends that; a missing Host (HTTP/1.0) is let through, since no browser sends one.
  bool _loopbackHost(HttpRequest request) {
    final host = request.headers.host;
    if (host == null) return true;
    final name = host.toLowerCase();
    return name == '127.0.0.1' || name == 'localhost' || name == '::1' || name == '[::1]';
  }

  Future<void> _handleStatus(HttpRequest request) async {
    final config = configStore.load();
    final body = <String, Object?>{
      'authenticated': config.authToken != null && config.authToken!.isNotEmpty,
      'backend_connected': isBackendConnected(),
      'credentials_rejected': isCredentialsRejected(),
      'last_close_code': lastCloseCode(),
    };
    // Without the token, only the booleans above (INT-037): the granted root paths say what on
    // this machine the agent can reach, and are no business of an arbitrary local process.
    if (_authorized(request)) {
      body['backend_url'] = config.backendUrl;
      body['roots'] = workspaceStore.load().all.map((r) => r.toJson()).toList();
      body['remote_control'] = config.remoteControl;
    }
    await _respond(request, 200, body);
  }

  /// `POST /remote_control` `{"enabled": <bool>}` (WP8, D-10b): the desktop's "Allow control
  /// from my phone and the web" switch, persisted to `config.json`. This route -- token-guarded
  /// and loopback-only like every other mutating one -- is the only way to change it; no RPC from
  /// the backend can. The agent's `TaskHandler` reads it from disk on every call of a remote task,
  /// so switching it off refuses that task's next call.
  Future<void> _handleRemoteControl(HttpRequest request) async {
    final Object? decoded;
    try {
      final raw = await utf8.decoder.bind(request).join();
      decoded = raw.trim().isEmpty ? null : jsonDecode(raw);
    } on FormatException {
      await _fail(request, 400, HostdErrorCode.invalidRequestBody, 'the body must be JSON: {"enabled": true|false}');
      return;
    }
    final enabled = decoded is Map ? decoded['enabled'] : null;
    if (enabled is! bool) {
      await _fail(request, 400, HostdErrorCode.missingParameter, '"enabled" is required and must be true or false');
      return;
    }
    configStore.save(configStore.load().copyWith(remoteControl: enabled));
    _logger('remote control ${enabled ? 'enabled' : 'disabled'} from the desktop');
    await _respond(request, 200, {'remote_control': enabled});
  }

  Future<void> _handleListRoots(HttpRequest request) async {
    final roots = workspaceStore.load();
    await _respond(request, 200, {'roots': roots.all.map((r) => r.toJson()).toList()});
  }

  Future<void> _handleRootAction(HttpRequest request) async {
    final body = await _readJsonBody(request);
    final action = body['action'] as String?;
    final path = body['path'] as String?;
    if (action == null || path == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'action and path are required');
      return;
    }
    final roots = workspaceStore.load();
    switch (action) {
      case 'grant':
        try {
          final root = roots.addRoot(path, trusted: body['trusted'] as bool? ?? false);
          workspaceStore.save(roots);
          await _respond(request, 200, {'root': root.toJson()});
        } on InvalidRootError catch (error) {
          await _fail(request, 400, error.code, error.message);
        }
      case 'revoke':
        final removed = roots.removeRoot(path);
        if (removed) workspaceStore.save(roots);
        await _respond(request, removed ? 200 : 404, {'removed': removed});
      case 'trust':
      case 'untrust':
        final updated = roots.setTrusted(path, action == 'trust');
        if (updated == null) {
          await _fail(request, 404, HostdErrorCode.rootNotGranted, '$path is not a granted root');
          return;
        }
        workspaceStore.save(roots);
        await _respond(request, 200, {'root': updated.toJson()});
      default:
        await _fail(request, 400, HostdErrorCode.unknownAction, 'unknown action "$action"');
    }
  }

  /// Directory listing for the file-tree browser -- entries only, no content (see `_handleRead`
  /// for that), and refuses (via [WorkspaceRoots.requireContained]) anything that would resolve
  /// outside every granted root exactly like every other filesystem RPC in this daemon.
  Future<void> _handleList(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    final relative = request.uri.queryParameters['path'] ?? '';
    if (rootParam == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root query parameter is required');
      return;
    }
    final roots = workspaceStore.load();
    try {
      final root = roots.all.firstWhere(
        (r) => r.canonicalPath == rootParam,
        orElse: () => throw RootNotFoundError(rootParam),
      );
      final resolved = roots.requireContained(root, relative.isEmpty ? '.' : relative);
      final dir = Directory(resolved);
      if (!dir.existsSync()) {
        await _fail(request, 404, HostdErrorCode.pathNotFound, '$relative does not exist');
        return;
      }
      final entries = dir
          .listSync(followLinks: false)
          .map((entity) => {
                'name': p.basename(entity.path),
                'is_directory': entity is Directory,
              })
          .toList()
        ..sort((a, b) {
          final aDir = a['is_directory'] as bool;
          final bDir = b['is_directory'] as bool;
          if (aDir != bDir) return aDir ? -1 : 1;
          return (a['name'] as String).compareTo(b['name'] as String);
        });
      await _respond(request, 200, {'root': root.canonicalPath, 'path': relative, 'entries': entries});
    } on ContainmentViolation catch (error) {
      await _fail(request, 403, error.code, error.message);
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    }
  }

  /// Read-only file content for the browser -- never a write path. Refuses anything outside a
  /// granted root, and caps how much of a file it will ever read into memory ([_maxReadBytes]).
  Future<void> _handleRead(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    final relative = request.uri.queryParameters['path'];
    if (rootParam == null || relative == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root and path query parameters are required');
      return;
    }
    final roots = workspaceStore.load();
    try {
      final root = roots.all.firstWhere(
        (r) => r.canonicalPath == rootParam,
        orElse: () => throw RootNotFoundError(rootParam),
      );
      final resolved = roots.requireContained(root, relative);
      final file = File(resolved);
      if (!file.existsSync()) {
        await _fail(request, 404, HostdErrorCode.pathNotFound, '$relative does not exist');
        return;
      }
      final size = file.lengthSync();
      final bytes = size > _maxReadBytes
          ? (await file.openRead(0, _maxReadBytes).toList()).expand((chunk) => chunk).toList()
          : file.readAsBytesSync();
      String content;
      try {
        content = utf8.decode(bytes);
      } on FormatException {
        await _fail(request, 415, HostdErrorCode.fileNotUtf8, 'file is not valid UTF-8 text');
        return;
      }
      await _respond(request, 200, {
        'content': content,
        'truncated': size > _maxReadBytes,
        'size': size,
      });
    } on ContainmentViolation catch (error) {
      await _fail(request, 403, error.code, error.message);
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    }
  }

  /// Upgrades to a WebSocket for an interactive terminal session (D4,
  /// docs/architecture/015-desktop-ide.md) and hands it straight to
  /// [terminalSessions] -- gated by the same bearer token as every route
  /// here except `/status` (checked in `_handle` before this ever runs), with
  /// the workspace-trust check happening inside `TerminalSessionManager`
  /// itself so a rejection reaches the client as a clean `error` frame.
  Future<void> _handleTerminalUpgrade(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    if (rootParam == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root query parameter is required');
      return;
    }
    final socket = await WebSocketTransformer.upgrade(request);
    unawaited(terminalSessions.handleConnection(socket, rootPath: rootParam));
  }

  /// D7 Part A.3: upgrades to a WebSocket for a Debug Console session, handed straight to
  /// [debugSessions] -- same shape as [_handleTerminalUpgrade] above, since spawning a
  /// debuggable process and streaming DAP bytes bidirectionally is the same problem the terminal
  /// already solved (trust-check happens inside [DebugSessionManager] itself).
  Future<void> _handleDebugUpgrade(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    if (rootParam == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root query parameter is required');
      return;
    }
    final socket = await WebSocketTransformer.upgrade(request);
    unawaited(debugSessions.handleConnection(socket, rootPath: rootParam));
  }

  /// D5's approval queue (`approval_queue.dart`), read side -- lists every currently-pending
  /// approval a Desktop IDE session should surface to the user. Only [PendingApproval.displayCommand]
  /// (redacted) ever leaves this process; [PendingApproval.canonicalCommand] is never serialized.
  Future<void> _handleApprovalsList(HttpRequest request) async {
    await _respond(request, 200, {'approvals': approvalQueue.list().map((a) => a.toJson()).toList()});
  }

  /// D5's approval queue, write side -- `POST /approvals/<id>/respond` with body
  /// `{"approved": bool}`. `404` for an id that was never created; `200` idempotently for one
  /// that already resolved (approved/rejected/expired), matching [ApprovalQueue.respond]'s own
  /// idempotency so a duplicate click from the UI is harmless rather than an error.
  Future<void> _handleApprovalRespond(HttpRequest request, String id) async {
    final body = await _readJsonBody(request);
    final approved = body['approved'];
    if (approved is! bool) {
      await _fail(request, 400, HostdErrorCode.missingParameter, '"approved" (bool) is required');
      return;
    }
    final found = approvalQueue.respond(id, approved);
    if (!found) {
      await _fail(request, 404, HostdErrorCode.approvalNotFound, 'no such pending approval (resolved or expired)');
      return;
    }
    await _respond(request, 200, {'ok': true});
  }

  /// D5's Source Control panel, read routes -- see `git_service.dart`'s doc comment for why
  /// these (and the write routes below) are a hard human-only boundary, never reachable via
  /// `TaskHandler`/the approval queue.
  Future<void> _handleGitStatus(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    if (rootParam == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root query parameter is required');
      return;
    }
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      await _respond(request, 200, await gitService.status(root.canonicalPath));
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  /// `POST /git/init` `{"root": <path>}` (WP4, D-13): "Initialize git here" from the desktop app.
  /// [root] must be exactly a granted root; one that already is a repository with a commit is a
  /// 409. Otherwise `git init`, `git add -A` and a first commit with the person's own git identity
  /// (`AURA <aura@localhost>` only if git has none) -- `git_init.dart`, the same as the `init_git`
  /// RPC the phone and the web use. Responds `{"ok": true, "commit": <sha>}`.
  Future<void> _handleGitInit(HttpRequest request) async {
    final body = await _readJsonBody(request);
    final root = body['root'];
    if (root is! String || root.isEmpty) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root is required');
      return;
    }
    try {
      await _respond(request, 200, await initializeGrantedFolder(workspaceStore.load(), gitService, root));
    } on GitInitRefusal catch (error) {
      final status = switch (error.code) {
        HostdErrorCode.rootNotGranted => 404,
        HostdErrorCode.alreadyAGitRepo => 409,
        _ => 500,
      };
      await _fail(request, status, error.code, error.message, detail: error.detail);
    }
  }

  Future<void> _handleGitDiff(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    if (rootParam == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root query parameter is required');
      return;
    }
    final relativeFile = request.uri.queryParameters['file'];
    final scope =
        request.uri.queryParameters['staged'] == 'true' ? GitDiffScope.staged : GitDiffScope.workingTree;
    final roots = workspaceStore.load();
    try {
      final root = _requireRoot(roots, rootParam);
      final resolvedFile = relativeFile == null ? null : roots.requireContained(root, relativeFile);
      await _respond(request, 200, await gitService.diff(root.canonicalPath, file: resolvedFile, scope: scope));
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    } on ContainmentViolation catch (error) {
      await _fail(request, 403, error.code, error.message);
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  Future<void> _handleGitLog(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    if (rootParam == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root query parameter is required');
      return;
    }
    final limit = int.tryParse(request.uri.queryParameters['limit'] ?? '') ?? 30;
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      await _respond(request, 200, await gitService.log(root.canonicalPath, limit: limit));
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  /// D5's Source Control panel, write routes -- reachable only by a human's own click in the Git
  /// panel, so unlike every agent-facing command these run unconditionally: no `root.trusted`
  /// check, no approval queue (design decision 1).
  Future<void> _handleGitStage(HttpRequest request, {required bool unstage}) async {
    final body = await _readJsonBody(request);
    final rootParam = body['root'] as String?;
    if (rootParam == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root is required');
      return;
    }
    final all = body['all'] == true;
    final rawPaths = (body['paths'] as List?)?.cast<Object?>().map((e) => e.toString()).toList() ?? const [];
    if (!all && rawPaths.isEmpty) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'paths (or all: true) is required');
      return;
    }
    final roots = workspaceStore.load();
    try {
      final root = _requireRoot(roots, rootParam);
      final paths = all ? ['.'] : rawPaths.map((relative) => roots.requireContained(root, relative)).toList();
      final result = unstage
          ? await gitService.unstage(root.canonicalPath, paths)
          : await gitService.stage(root.canonicalPath, paths);
      await _respond(request, 200, result);
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    } on ContainmentViolation catch (error) {
      await _fail(request, 403, error.code, error.message);
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  Future<void> _handleGitCommit(HttpRequest request) async {
    final body = await _readJsonBody(request);
    final rootParam = body['root'] as String?;
    final message = body['message'] as String?;
    if (rootParam == null || message == null || message.trim().isEmpty) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root and a non-empty message are required');
      return;
    }
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      final result = await gitService.commit(root.canonicalPath, message);
      await _respond(request, result['ok'] == false ? 409 : 200, result);
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  Future<void> _handleGitCheckout(HttpRequest request) async {
    final body = await _readJsonBody(request);
    final rootParam = body['root'] as String?;
    final branch = body['branch'] as String?;
    final create = body['create'] == true;
    if (rootParam == null || branch == null || branch.trim().isEmpty) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root and branch are required');
      return;
    }
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      final result = await gitService.checkout(root.canonicalPath, branch, create: create);
      await _respond(request, result['ok'] == false ? 409 : 200, result);
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  /// D5 Git panel: merges [branch] into the human's own checked-out working tree -- same
  /// human-only boundary as `/git/checkout` above (a click is the consent), distinct from
  /// `/git/merge-task` below which only ever targets a task's isolated worktree.
  Future<void> _handleGitMerge(HttpRequest request) async {
    final body = await _readJsonBody(request);
    final rootParam = body['root'] as String?;
    final branch = body['branch'] as String?;
    if (rootParam == null || branch == null || branch.trim().isEmpty) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root and branch are required');
      return;
    }
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      final result = await gitService.mergeBranch(root.canonicalPath, branch);
      await _respond(request, result['ok'] == false ? 409 : 200, result);
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  Future<void> _handleGitMergeAbort(HttpRequest request) async {
    final body = await _readJsonBody(request);
    final rootParam = body['root'] as String?;
    if (rootParam == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root is required');
      return;
    }
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      final result = await gitService.abortMerge(root.canonicalPath);
      await _respond(request, 200, result);
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  /// `GET /git/remotes?root=<path>` -- the configured remotes, for "Publish branch" to offer. Read-only.
  Future<void> _handleGitRemotes(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    if (rootParam == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root query parameter is required');
      return;
    }
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      await _respond(request, 200, await gitService.remotes(root.canonicalPath));
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  Future<void> _handleGitBranches(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    if (rootParam == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root query parameter is required');
      return;
    }
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      await _respond(request, 200, await gitService.branches(root.canonicalPath));
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  /// Human-only remote-sync routes (`/git/fetch`, `/pull`, `/push`, `/publish`) -- same boundary as
  /// the other `/git/*` write routes above (a click in the Git panel is the consent; no
  /// `root.trusted` check, no approval queue). `push` takes no refspec: it runs the person's own
  /// `git push`, so what it sends follows their git configuration like a terminal's (decision D-03).
  /// `publish` takes only the name of a configured remote and pushes the checked-out branch to it
  /// under its own name, setting the upstream (decision D-01).
  Future<void> _handleGitRemoteOp(HttpRequest request, String op) async {
    final body = await _readJsonBody(request);
    final rootParam = body['root'] as String?;
    if (rootParam == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root is required');
      return;
    }
    final remoteParam = body['remote'];
    if (op == 'publish' && (remoteParam is! String || remoteParam.isEmpty)) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'remote is required');
      return;
    }
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      final result = switch (op) {
        'fetch' => await gitService.fetch(root.canonicalPath),
        'pull' => await gitService.pull(root.canonicalPath),
        'push' => await gitService.push(root.canonicalPath),
        'publish' => await gitService.publish(root.canonicalPath, remote: remoteParam! as String),
        _ => throw StateError('unknown git remote op: $op'),
      };
      await _respond(request, result['ok'] == false ? 409 : 200, result);
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  Future<void> _handleGitFetch(HttpRequest request) => _handleGitRemoteOp(request, 'fetch');

  Future<void> _handleGitPull(HttpRequest request) => _handleGitRemoteOp(request, 'pull');

  Future<void> _handleGitPush(HttpRequest request) => _handleGitRemoteOp(request, 'push');

  Future<void> _handleGitPublish(HttpRequest request) => _handleGitRemoteOp(request, 'publish');

  /// D7 Part A.2. No trust/approval gate: a lint run mutates nothing, the same precedent
  /// `/git/status` etc. already establish for read-only human-only routes.
  Future<void> _handleDiagnosticsDetect(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    if (rootParam == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root query parameter is required');
      return;
    }
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      final tool = diagnosticsService.detect(root.canonicalPath);
      await _respond(request, 200, {'tool': tool});
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    }
  }

  Future<void> _handleDiagnosticsRun(HttpRequest request) async {
    final body = await _readJsonBody(request);
    final rootParam = body['root'] as String?;
    final tool = body['tool'] as String?;
    if (rootParam == null || tool == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root and tool are required');
      return;
    }
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      final result = await diagnosticsService.run(root.canonicalPath, tool, trusted: root.trusted);
      await _respond(request, 200, {
        'tool': result.tool,
        'exit_code': result.exitCode,
        'stdout': result.stdout,
        'stderr': result.stderr,
        'truncated': result.truncated,
        'not_found': result.notFound,
        'timed_out': result.timedOut,
      });
    } on DiagnosticsNotTrustedError catch (error) {
      // G-17 (SEC-10): see `diagnostics_service.dart`'s doc comment.
      await _fail(request, 403, HostdErrorCode.diagnosticsUntrusted, error.toString());
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    } on ArgumentError catch (error) {
      await _fail(request, 400, HostdErrorCode.unknownDiagnosticsTool, '${error.message}');
    }
  }

  /// D9's "Agent Review" panel data source -- every isolated task this daemon currently knows
  /// about. No trust/approval gate: listing metadata about a task's own worktree mutates nothing
  /// and reveals nothing beyond what the Desktop IDE already shows for the parent project itself.
  Future<void> _handleTasksList(HttpRequest request) async {
    await _respond(request, 200, {'tasks': isolatedTasks.list().map((t) => t.toJson()).toList()});
  }

  /// D9: the branch-range diff between a task's branch and [base] (whatever ref the reviewer is
  /// comparing against right now, typically the main worktree's current branch -- not something
  /// recorded at task-creation time, since a merge target can move; see `git_service.dart`'s
  /// `diff` doc comment on why triple-dot notation makes that the right comparison regardless).
  Future<void> _handleGitTaskDiff(HttpRequest request) async {
    final taskKey = request.uri.queryParameters['task_key'];
    final base = request.uri.queryParameters['base'];
    if (taskKey == null || base == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'task_key and base query parameters are required');
      return;
    }
    final info = isolatedTasks.infoFor(taskKey);
    if (info == null) {
      await _fail(request, 404, HostdErrorCode.taskNotFound, 'no isolated workspace is known for task_key "$taskKey"');
      return;
    }
    try {
      final result = await gitService.diff(info.root.canonicalPath, scope: GitDiffScope.branchRange, base: base);
      await _respond(request, 200, result);
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  Future<void> _handleGitTaskDiffStat(HttpRequest request) async {
    final taskKey = request.uri.queryParameters['task_key'];
    final base = request.uri.queryParameters['base'];
    if (taskKey == null || base == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'task_key and base query parameters are required');
      return;
    }
    final info = isolatedTasks.infoFor(taskKey);
    if (info == null) {
      await _fail(request, 404, HostdErrorCode.taskNotFound, 'no isolated workspace is known for task_key "$taskKey"');
      return;
    }
    try {
      final result = await gitService.diffStat(info.root.canonicalPath, base);
      await _respond(request, 200, result);
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  /// D9: human-only, local-only merge of a task's branch into whatever the human currently has
  /// checked out in [rootParam]'s main worktree -- structurally identical to `/git/commit`/
  /// `/git/checkout` above (a human's own click is the consent: no approval queue, no trust
  /// check), and entirely unrelated to `git_push`'s refusal to push the default branch, which
  /// guards the *agent's own* `git_push` tool call, never invoked here.
  Future<void> _handleGitMergeTask(HttpRequest request) async {
    final body = await _readJsonBody(request);
    final rootParam = body['root'] as String?;
    final taskBranch = body['task_branch'] as String?;
    if (rootParam == null || taskBranch == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'root and task_branch are required');
      return;
    }
    final squash = body['squash'] == true;
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      final result = await gitService.mergeTask(root.canonicalPath, taskBranch, squash: squash);
      await _respond(request, result['ok'] == false ? 409 : 200, result);
    } on RootNotFoundError {
      await _fail(request, 404, HostdErrorCode.rootNotGranted, '$rootParam is not a granted root');
    } on GitNotARepositoryError {
      await _fail(request, 409, HostdErrorCode.notAGitRepository, 'not a git repository');
    } on GitDecodeError catch (error) {
      await _fail(request, 415, HostdErrorCode.gitOutputNotUtf8, error.message);
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  /// D9: tears down an isolated task's worktree/branch entirely -- a human's explicit "Discard,"
  /// behind a mandatory confirmation dialog client-side (this destroys unpushed work). Never
  /// reachable via the agent-facing RPC surface.
  Future<void> _handleGitDiscardTask(HttpRequest request) async {
    final body = await _readJsonBody(request);
    final taskKey = body['task_key'] as String?;
    if (taskKey == null) {
      await _fail(request, 400, HostdErrorCode.missingParameter, 'task_key is required');
      return;
    }
    try {
      final result = await isolatedTasks.discard(taskKey);
      await _respond(request, 200, result);
    } on TaskNotFoundError catch (error) {
      await _fail(request, 404, HostdErrorCode.taskNotFound, error.toString());
    } on GitCommandError catch (error) {
      await _fail(request, 500, HostdErrorCode.gitCommandFailed, error.message);
    }
  }

  WorkspaceRoot _requireRoot(WorkspaceRoots roots, String rootParam) {
    return roots.all.firstWhere(
      (r) => r.canonicalPath == rootParam,
      orElse: () => throw RootNotFoundError(rootParam),
    );
  }

  /// Starts a pairing session (`pairing_client.dart::startPairing`, the same first step
  /// `aura_hostd auth pair` runs) and returns the code immediately -- unlike a single blocking
  /// `/pair` call, this must return *before* approval happens, since the whole point is for a UI
  /// in a different process to show the code to the person who is about to go approve it. Poll
  /// `GET /pair/status` for the outcome.
  Future<void> _handlePairStart(HttpRequest request) async {
    final body = await _readJsonBody(request);
    final backendUrl = body['backend_url'] as String? ?? configStore.load().backendUrl;
    // F53: a token is never paired over (and later sent to) a plain-http host elsewhere.
    try {
      requireSecureBackendUrl(backendUrl);
    } on InvalidBackendUrlError catch (error) {
      await _fail(request, 400, HostdErrorCode.invalidRequestBody, error.message);
      return;
    }
    try {
      final session = await startPairing(backendUrl);
      _pairingSession = session;
      _pairingBackendUrl = backendUrl;
      _pairingOutcome = null;
      _scheduleBackgroundPoll(session, DateTime.now().add(Duration(seconds: session.expiresIn)));
      await _respond(request, 200, {
        'user_code': session.userCode,
        'expires_in': session.expiresIn,
        'interval': session.intervalS,
      });
    } on PairingFailedError catch (error) {
      await _fail(request, 502, HostdErrorCode.pairingFailed, error.message);
    }
  }

  /// Reports where the session started by `/pair/start` stands. The daemon polls the backend
  /// itself in the background (INT-038), so the token is collected even if the UI that started
  /// pairing goes away; this route polls once more on top of that (sharing one in-flight poll
  /// with the background loop) so a UI polling at the session's interval sees the outcome at
  /// once. On approval the token is saved (as `auth set-token` would) and [onPaired] is called,
  /// exactly once, whichever poll saw it first.
  Future<void> _handlePairStatus(HttpRequest request) async {
    if (_pairingSession == null) {
      final outcome = _pairingOutcome;
      if (outcome != null) {
        await _respond(request, 200, outcome);
      } else {
        await _fail(request, 409, HostdErrorCode.noPairingSession,
            'no pairing session in progress -- call /pair/start first');
      }
      return;
    }
    final outcome = await _pollPairing();
    await _respond(request, 200, outcome ?? _pairingOutcome ?? {'status': 'pending'});
  }

  void _scheduleBackgroundPoll(PairingSession session, DateTime deadline) {
    _pairingTimer?.cancel();
    final advised = Duration(seconds: session.intervalS);
    final interval = advised < _minPairingPollInterval ? _minPairingPollInterval : advised;
    _pairingTimer = Timer(interval, () async {
      _pairingTimer = null;
      if (!identical(_pairingSession, session)) return;
      if (DateTime.now().isAfter(deadline)) {
        _pairingSession = null;
        _pairingBackendUrl = null;
        _pairingOutcome = {
          'status': 'expired',
          'reason': 'pairing code expired before it was approved',
          'code': HostdErrorCode.pairingEnded,
        };
        return;
      }
      try {
        await _pollPairing();
      } catch (error) {
        // A transient backend or network failure; keep polling until the code expires.
        _logger('pairing poll failed: $error');
      }
      if (identical(_pairingSession, session)) _scheduleBackgroundPoll(session, deadline);
    });
  }

  /// Polls the backend once for the current session, or joins the poll already in flight.
  /// Returns the response body for a pending/approved/expired outcome, or `null` when the
  /// session was replaced or finished while this poll was out.
  Future<Map<String, Object?>?> _pollPairing() {
    final inFlight = _pollInFlight;
    if (inFlight != null) return inFlight;
    final future = _pollPairingOnce();
    _pollInFlight = future;
    return future.whenComplete(() => _pollInFlight = null);
  }

  Future<Map<String, Object?>?> _pollPairingOnce() async {
    final session = _pairingSession;
    final backendUrl = _pairingBackendUrl;
    if (session == null || backendUrl == null) return _pairingOutcome;
    final outcome = await pollPairingOnce(backendUrl, session);
    if (!identical(_pairingSession, session)) return _pairingOutcome;
    switch (outcome) {
      case PairingPending():
        return {'status': 'pending'};
      case PairingApproved(result: final result):
        _pairingSession = null;
        _pairingBackendUrl = null;
        _pairingTimer?.cancel();
        _pairingTimer = null;
        configStore.saveCredentials(backendUrl: backendUrl, authToken: result.accessToken);
        final body = <String, Object?>{'status': 'approved', 'device_id': result.deviceId};
        _pairingOutcome = body;
        onPaired(result.accessToken);
        return body;
      case PairingGone(reason: final reason):
        _pairingSession = null;
        _pairingBackendUrl = null;
        _pairingTimer?.cancel();
        _pairingTimer = null;
        final body = <String, Object?>{
          'status': 'expired',
          'reason': reason,
          'code': HostdErrorCode.pairingEnded,
        };
        _pairingOutcome = body;
        return body;
    }
  }

  Future<Map<String, dynamic>> _readJsonBody(HttpRequest request) async {
    final raw = await utf8.decoder.bind(request).join();
    if (raw.trim().isEmpty) return {};
    final decoded = jsonDecode(raw);
    return decoded is Map<String, dynamic> ? decoded : {};
  }

  /// Every error response: the English [message] under `error` (what apps predating codes
  /// show), plus its stable machine [code] (`error_codes.dart`), which a newer app translates.
  /// [detail]: the underlying error alone (git's own message for `git_init_failed`), so a
  /// translated message can quote it.
  Future<void> _fail(HttpRequest request, int statusCode, String code, String message, {String? detail}) =>
      _respond(request, statusCode, {'error': message, 'code': code}..addAll({if (detail != null) 'detail': detail}));

  Future<void> _respond(HttpRequest request, int statusCode, Map<String, Object?> body) async {
    assert(
      !body.containsKey('error') || body.containsKey('code'),
      'an error response must carry a code -- use _fail',
    );
    request.response.statusCode = statusCode;
    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode(body));
    await request.response.close();
  }
}

class RootNotFoundError implements Exception {
  RootNotFoundError(this.root);
  final String root;
}

typedef ControlServerLogger = void Function(String message);

void _defaultLogger(String message) => stdout.writeln('[aura_hostd] $message');

/// Compares two strings in time that depends only on their lengths, not on where they first
/// differ (INT-037), so the control token cannot be recovered a character at a time by timing.
bool constantTimeEquals(String a, String b) {
  final x = utf8.encode(a);
  final y = utf8.encode(b);
  var diff = x.length ^ y.length;
  final n = x.length < y.length ? x.length : y.length;
  for (var i = 0; i < n; i++) {
    diff |= x[i] ^ y[i];
  }
  return diff == 0;
}

String _randomToken() {
  final random = Random.secure();
  final bytes = List<int>.generate(32, (_) => random.nextInt(256));
  return base64UrlEncode(bytes).replaceAll('=', '');
}
