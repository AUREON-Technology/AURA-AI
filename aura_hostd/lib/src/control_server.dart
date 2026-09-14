/// A loopback-only local control server so a UI running as a *separate process* on the same
/// machine (Unit 8, `aura_client`'s desktop Engineer workspace screen) can supervise this daemon
/// and browse granted workspaces -- without that UI needing to be Dart code linked into this
/// package (deliberately not a dependency of `aura_client`, see this package's own pubspec doc
/// comment) or needing to shell out to the CLI and scrape stdout for every query.
///
/// Binds `127.0.0.1` on an ephemeral port and writes `{port, token}` to `control.json` in the
/// daemon's config dir so a same-machine process can discover it; the token (a random, per-run
/// secret) is required on every request except `GET /status`, and is the only thing standing
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
import 'git_service.dart';
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
    required this.approvalQueue,
    GitService? gitService,
    ControlServerLogger? logger,
  })  : gitService = gitService ?? GitService(),
        _logger = logger ?? _defaultLogger;

  final WorkspaceStore workspaceStore;
  final ConfigStore configStore;
  final TerminalSessionManager terminalSessions;

  /// D5's interactive consent queue -- the same instance `TaskHandler` queues commands into, so
  /// `GET /approvals` and `POST /approvals/<id>/respond` here act on exactly what `run` is
  /// waiting on. See `approval_queue.dart`.
  final ApprovalQueue approvalQueue;

  /// D5's direct-loopback git execution for the Source Control panel -- see `git_service.dart`'s
  /// doc comment for why this is a hard human-only boundary, never reachable via `TaskHandler`.
  final GitService gitService;

  /// Called once a `GET /pair/status` poll finds the pairing approved -- the caller (`bin/
  /// aura_hostd.dart`) uses this to start the backend WebSocket connection if it wasn't already
  /// running (the daemon can be started with `run` before any token exists, precisely so this
  /// control server -- and thus a UI driving it -- is reachable to pair through).
  final void Function(String accessToken) onPaired;

  final ControlServerLogger _logger;

  HttpServer? _server;
  String? _token;
  File? _controlFile;

  /// The one in-flight pairing session, if any -- only one pairing attempt makes sense at a time
  /// for a single local daemon with a single local UI driving it, so there is no session id to
  /// juggle: `/pair/start` replaces whatever was here, `/pair/status` always means "this one."
  PairingSession? _pairingSession;
  String? _pairingBackendUrl;

  int? get port => _server?.port;

  Future<void> start(Directory configDir) async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server = server;
    _token = _randomToken();
    _controlFile = File(p.join(configDir.path, 'control.json'));
    _controlFile!.parent.createSync(recursive: true);
    _controlFile!.writeAsStringSync(jsonEncode({'port': server.port, 'token': _token}));
    _logger('control server listening on 127.0.0.1:${server.port}');
    server.listen(_handle, onError: (Object e) => _logger('control server error: $e'));
  }

  Future<void> stop() async {
    try {
      _controlFile?.deleteSync();
    } on FileSystemException {
      // Already gone -- fine.
    }
    await _server?.close(force: true);
  }

  Future<void> _handle(HttpRequest request) async {
    try {
      if (request.uri.path != '/status' && !_authorized(request)) {
        await _respond(request, 401, {'error': 'missing or invalid control token'});
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
        case 'GET /list':
          await _handleList(request);
        case 'GET /read':
          await _handleRead(request);
        case 'GET /terminal':
          await _handleTerminalUpgrade(request);
        case 'GET /approvals':
          await _handleApprovalsList(request);
        case 'GET /git/status':
          await _handleGitStatus(request);
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
        case 'POST /pair/start':
          await _handlePairStart(request);
        case 'GET /pair/status':
          await _handlePairStatus(request);
        case 'POST /shutdown':
          await _respond(request, 200, {'status': 'shutting down'});
          unawaited(Future(() => exit(0)));
        default:
          await _respond(request, 404, {'error': 'no such endpoint'});
      }
    } catch (error) {
      _logger('control server: unhandled error: $error');
      try {
        await _respond(request, 500, {'error': error.toString()});
      } catch (_) {
        // Response already closed -- nothing more to do.
      }
    }
  }

  bool _authorized(HttpRequest request) {
    final header = request.headers.value(HttpHeaders.authorizationHeader);
    return header != null && header == 'Bearer $_token';
  }

  Future<void> _handleStatus(HttpRequest request) async {
    final config = configStore.load();
    final roots = workspaceStore.load();
    await _respond(request, 200, {
      'authenticated': config.authToken != null && config.authToken!.isNotEmpty,
      'backend_url': config.backendUrl,
      'roots': roots.all.map((r) => r.toJson()).toList(),
    });
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
      await _respond(request, 400, {'error': 'action and path are required'});
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
          await _respond(request, 400, {'error': error.message});
        }
      case 'revoke':
        final removed = roots.removeRoot(path);
        if (removed) workspaceStore.save(roots);
        await _respond(request, removed ? 200 : 404, {'removed': removed});
      case 'trust':
      case 'untrust':
        final updated = roots.setTrusted(path, action == 'trust');
        if (updated == null) {
          await _respond(request, 404, {'error': '$path is not a granted root'});
          return;
        }
        workspaceStore.save(roots);
        await _respond(request, 200, {'root': updated.toJson()});
      default:
        await _respond(request, 400, {'error': 'unknown action "$action"'});
    }
  }

  /// Directory listing for the file-tree browser -- entries only, no content (see `_handleRead`
  /// for that), and refuses (via [WorkspaceRoots.requireContained]) anything that would resolve
  /// outside every granted root exactly like every other filesystem RPC in this daemon.
  Future<void> _handleList(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    final relative = request.uri.queryParameters['path'] ?? '';
    if (rootParam == null) {
      await _respond(request, 400, {'error': 'root query parameter is required'});
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
        await _respond(request, 404, {'error': '$relative does not exist'});
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
      await _respond(request, 403, {'error': error.message});
    } on RootNotFoundError {
      await _respond(request, 404, {'error': '$rootParam is not a granted root'});
    }
  }

  /// Read-only file content for the browser -- never a write path. Refuses anything outside a
  /// granted root, and caps how much of a file it will ever read into memory ([_maxReadBytes]).
  Future<void> _handleRead(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    final relative = request.uri.queryParameters['path'];
    if (rootParam == null || relative == null) {
      await _respond(request, 400, {'error': 'root and path query parameters are required'});
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
        await _respond(request, 404, {'error': '$relative does not exist'});
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
        await _respond(request, 415, {'error': 'file is not valid UTF-8 text'});
        return;
      }
      await _respond(request, 200, {
        'content': content,
        'truncated': size > _maxReadBytes,
        'size': size,
      });
    } on ContainmentViolation catch (error) {
      await _respond(request, 403, {'error': error.message});
    } on RootNotFoundError {
      await _respond(request, 404, {'error': '$rootParam is not a granted root'});
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
      await _respond(request, 400, {'error': 'root query parameter is required'});
      return;
    }
    final socket = await WebSocketTransformer.upgrade(request);
    unawaited(terminalSessions.handleConnection(socket, rootPath: rootParam));
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
      await _respond(request, 400, {'error': '"approved" (bool) is required'});
      return;
    }
    final found = approvalQueue.respond(id, approved);
    if (!found) {
      await _respond(request, 404, {'error': 'no such pending approval (resolved or expired)'});
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
      await _respond(request, 400, {'error': 'root query parameter is required'});
      return;
    }
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      await _respond(request, 200, await gitService.status(root.canonicalPath));
    } on RootNotFoundError {
      await _respond(request, 404, {'error': '$rootParam is not a granted root'});
    } on GitNotARepositoryError {
      await _respond(request, 409, {'error': 'not a git repository'});
    } on GitDecodeError catch (error) {
      await _respond(request, 415, {'error': error.message});
    } on GitCommandError catch (error) {
      await _respond(request, 500, {'error': error.message});
    }
  }

  Future<void> _handleGitDiff(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    if (rootParam == null) {
      await _respond(request, 400, {'error': 'root query parameter is required'});
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
      await _respond(request, 404, {'error': '$rootParam is not a granted root'});
    } on ContainmentViolation catch (error) {
      await _respond(request, 403, {'error': error.message});
    } on GitNotARepositoryError {
      await _respond(request, 409, {'error': 'not a git repository'});
    } on GitDecodeError catch (error) {
      await _respond(request, 415, {'error': error.message});
    } on GitCommandError catch (error) {
      await _respond(request, 500, {'error': error.message});
    }
  }

  Future<void> _handleGitLog(HttpRequest request) async {
    final rootParam = request.uri.queryParameters['root'];
    if (rootParam == null) {
      await _respond(request, 400, {'error': 'root query parameter is required'});
      return;
    }
    final limit = int.tryParse(request.uri.queryParameters['limit'] ?? '') ?? 30;
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      await _respond(request, 200, await gitService.log(root.canonicalPath, limit: limit));
    } on RootNotFoundError {
      await _respond(request, 404, {'error': '$rootParam is not a granted root'});
    } on GitNotARepositoryError {
      await _respond(request, 409, {'error': 'not a git repository'});
    } on GitDecodeError catch (error) {
      await _respond(request, 415, {'error': error.message});
    } on GitCommandError catch (error) {
      await _respond(request, 500, {'error': error.message});
    }
  }

  /// D5's Source Control panel, write routes -- reachable only by a human's own click in the Git
  /// panel, so unlike every agent-facing command these run unconditionally: no `root.trusted`
  /// check, no approval queue (design decision 1).
  Future<void> _handleGitStage(HttpRequest request, {required bool unstage}) async {
    final body = await _readJsonBody(request);
    final rootParam = body['root'] as String?;
    if (rootParam == null) {
      await _respond(request, 400, {'error': 'root is required'});
      return;
    }
    final all = body['all'] == true;
    final rawPaths = (body['paths'] as List?)?.cast<Object?>().map((e) => e.toString()).toList() ?? const [];
    if (!all && rawPaths.isEmpty) {
      await _respond(request, 400, {'error': 'paths (or all: true) is required'});
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
      await _respond(request, 404, {'error': '$rootParam is not a granted root'});
    } on ContainmentViolation catch (error) {
      await _respond(request, 403, {'error': error.message});
    } on GitNotARepositoryError {
      await _respond(request, 409, {'error': 'not a git repository'});
    } on GitDecodeError catch (error) {
      await _respond(request, 415, {'error': error.message});
    } on GitCommandError catch (error) {
      await _respond(request, 500, {'error': error.message});
    }
  }

  Future<void> _handleGitCommit(HttpRequest request) async {
    final body = await _readJsonBody(request);
    final rootParam = body['root'] as String?;
    final message = body['message'] as String?;
    if (rootParam == null || message == null || message.trim().isEmpty) {
      await _respond(request, 400, {'error': 'root and a non-empty message are required'});
      return;
    }
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      final result = await gitService.commit(root.canonicalPath, message);
      await _respond(request, result['ok'] == false ? 409 : 200, result);
    } on RootNotFoundError {
      await _respond(request, 404, {'error': '$rootParam is not a granted root'});
    } on GitNotARepositoryError {
      await _respond(request, 409, {'error': 'not a git repository'});
    } on GitDecodeError catch (error) {
      await _respond(request, 415, {'error': error.message});
    } on GitCommandError catch (error) {
      await _respond(request, 500, {'error': error.message});
    }
  }

  Future<void> _handleGitCheckout(HttpRequest request) async {
    final body = await _readJsonBody(request);
    final rootParam = body['root'] as String?;
    final branch = body['branch'] as String?;
    final create = body['create'] == true;
    if (rootParam == null || branch == null || branch.trim().isEmpty) {
      await _respond(request, 400, {'error': 'root and branch are required'});
      return;
    }
    try {
      final root = _requireRoot(workspaceStore.load(), rootParam);
      final result = await gitService.checkout(root.canonicalPath, branch, create: create);
      await _respond(request, result['ok'] == false ? 409 : 200, result);
    } on RootNotFoundError {
      await _respond(request, 404, {'error': '$rootParam is not a granted root'});
    } on GitNotARepositoryError {
      await _respond(request, 409, {'error': 'not a git repository'});
    } on GitDecodeError catch (error) {
      await _respond(request, 415, {'error': error.message});
    } on GitCommandError catch (error) {
      await _respond(request, 500, {'error': error.message});
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
    try {
      final session = await startPairing(backendUrl);
      _pairingSession = session;
      _pairingBackendUrl = backendUrl;
      await _respond(request, 200, {
        'user_code': session.userCode,
        'expires_in': session.expiresIn,
        'interval': session.intervalS,
      });
    } on PairingFailedError catch (error) {
      await _respond(request, 502, {'error': error.message});
    }
  }

  /// Polls the in-flight session started by `/pair/start` exactly once (`pairing_client.dart
  /// ::pollPairingOnce`) and reports where it stands -- the UI is responsible for calling this
  /// again after the session's own `interval`, matching the discipline `pairWithCode`'s CLI path
  /// applies to itself. On approval, saves the token (same as `auth set-token` would have) and
  /// calls [onPaired] so the daemon connects to the backend without needing `run` restarted.
  Future<void> _handlePairStatus(HttpRequest request) async {
    final session = _pairingSession;
    final backendUrl = _pairingBackendUrl;
    if (session == null || backendUrl == null) {
      await _respond(request, 409, {'error': 'no pairing session in progress -- call /pair/start first'});
      return;
    }
    final outcome = await pollPairingOnce(backendUrl, session);
    switch (outcome) {
      case PairingPending():
        await _respond(request, 200, {'status': 'pending'});
      case PairingApproved(result: final result):
        _pairingSession = null;
        _pairingBackendUrl = null;
        configStore.save(DaemonConfig(backendUrl: backendUrl, authToken: result.accessToken));
        onPaired(result.accessToken);
        await _respond(request, 200, {'status': 'approved', 'device_id': result.deviceId});
      case PairingGone(reason: final reason):
        _pairingSession = null;
        _pairingBackendUrl = null;
        await _respond(request, 200, {'status': 'expired', 'reason': reason});
    }
  }

  Future<Map<String, dynamic>> _readJsonBody(HttpRequest request) async {
    final raw = await utf8.decoder.bind(request).join();
    if (raw.trim().isEmpty) return {};
    final decoded = jsonDecode(raw);
    return decoded is Map<String, dynamic> ? decoded : {};
  }

  Future<void> _respond(HttpRequest request, int statusCode, Map<String, Object?> body) async {
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

String _randomToken() {
  final random = Random.secure();
  final bytes = List<int>.generate(32, (_) => random.nextInt(256));
  return base64UrlEncode(bytes).replaceAll('=', '');
}
