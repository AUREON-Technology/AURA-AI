/// HTTP client for `aura_hostd`'s local control server (`aura_hostd/lib/src/control_server.dart`,
/// ADR-011 Unit 8) -- a *separate process* on the same machine. This never talks to the AURA
/// backend; every call here is a loopback request to whatever port/token `control.json` names,
/// discovered fresh on every call rather than cached, since the daemon can be started, stopped,
/// or restarted (picking a new ephemeral port) independently of this app's own lifecycle.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'hostd_config_paths_io.dart';

/// Thrown when `aura_hostd` isn't reachable at all -- no control file, or nothing listening on
/// the port it names (the daemon crashed or was killed without cleaning up after itself).
class HostdControlUnavailable implements Exception {
  HostdControlUnavailable(this.message);

  final String message;

  @override
  String toString() => message;
}

/// Thrown when the control server answered but rejected the call (bad path, containment
/// violation, pairing not started yet, ...).
class HostdControlError implements Exception {
  HostdControlError(this.statusCode, this.message);

  final int statusCode;
  final String message;

  @override
  String toString() => 'HTTP $statusCode: $message';
}

class HostdControlEndpoint {
  HostdControlEndpoint({required this.port, required this.token});

  final int port;
  final String token;
}

/// Reads `control.json`; `null` means `aura_hostd` isn't running (or this platform has no fixed
/// config-dir convention to look in at all) -- not an error, just "nothing to talk to."
Future<HostdControlEndpoint?> discoverHostdControlEndpoint() async {
  final path = hostdControlFilePath();
  if (path == null) return null;
  final file = File(path);
  if (!file.existsSync()) return null;
  try {
    final decoded = jsonDecode(await file.readAsString());
    if (decoded is! Map<String, dynamic>) return null;
    final port = decoded['port'];
    final token = decoded['token'];
    if (port is! int || token is! String) return null;
    return HostdControlEndpoint(port: port, token: token);
  } on FormatException {
    return null;
  } on FileSystemException {
    return null;
  }
}

class HostdControlClient {
  HostdControlClient({
    http.Client? httpClient,
    @visibleForTesting Future<HostdControlEndpoint?> Function()? discoverEndpoint,
  })  : _http = httpClient ?? http.Client(),
        _discoverEndpoint = discoverEndpoint ?? discoverHostdControlEndpoint;

  final http.Client _http;
  final Future<HostdControlEndpoint?> Function() _discoverEndpoint;

  Future<Map<String, dynamic>> _request(
    String method,
    String path, {
    Map<String, Object?>? body,
    bool authorize = true,
  }) async {
    final endpoint = await _discoverEndpoint();
    if (endpoint == null) {
      throw HostdControlUnavailable(currentL10n.coreHostdNotRunning);
    }
    final uri = Uri.parse('http://127.0.0.1:${endpoint.port}$path');
    final headers = <String, String>{
      if (authorize) 'Authorization': 'Bearer ${endpoint.token}',
      if (body != null) 'Content-Type': 'application/json',
    };
    http.Response response;
    try {
      response = switch (method) {
        'GET' => await _http.get(uri, headers: headers),
        'POST' => await _http.post(uri, headers: headers, body: body == null ? null : jsonEncode(body)),
        _ => throw ArgumentError('unsupported method $method'),
      };
    } on http.ClientException catch (error) {
      throw HostdControlUnavailable(currentL10n.coreHostdUnreachable('$error'));
    } on SocketException catch (error) {
      throw HostdControlUnavailable(currentL10n.coreHostdUnreachable('$error'));
    }
    final decoded = response.body.isEmpty ? <String, dynamic>{} : jsonDecode(response.body);
    final decodedMap = decoded is Map<String, dynamic> ? decoded : <String, dynamic>{};
    if (response.statusCode < 200 || response.statusCode >= 300) {
      // The git write routes (commit/checkout/pull/push/merge) respond with a non-2xx status
      // *and* a structured `{'ok': false, 'reason': ..., ...}` body for an expected business
      // outcome (nothing staged, would overwrite local changes, diverged, conflict) -- distinct
      // from a transport/auth/not-found failure, which never sets `ok`. Discarding that body and
      // throwing a generic "HTTP 409" here (as this used to do unconditionally) silently broke
      // every one of those callers' structured-failure handling, since they never got to inspect
      // `result['reason']`/`result['conflicted_files']` -- found while wiring up `gitMerge`'s
      // conflict path (2026-09-25 audit follow-up), but it affects every existing caller that
      // already branches on `result['ok'] == false`.
      if (decodedMap['ok'] == false) {
        return decodedMap;
      }
      final message = (decodedMap['error'] as String?) ?? 'HTTP ${response.statusCode}';
      // `control_server.dart`'s unmatched-route fallback is this exact literal string -- distinct
      // from every other legitimate 404 in that file (a granted-root or path-not-found message).
      // A stale daemon binary running server code that predates a route the client now calls
      // reports it this way, which otherwise reaches the user as a bare, meaningless string.
      if (response.statusCode == 404 && message == 'no such endpoint') {
        throw HostdControlError(
          response.statusCode,
          currentL10n.coreHostdOutOfDate,
        );
      }
      throw HostdControlError(response.statusCode, message);
    }
    return decodedMap;
  }

  /// The one call that works even when `aura_hostd` has no auth token yet -- so the daemon
  /// status card can show "running, not paired" instead of a hard error.
  Future<Map<String, dynamic>> status() => _request('GET', '/status', authorize: false);

  Future<List<Map<String, dynamic>>> roots() async {
    final result = await _request('GET', '/roots');
    return (result['roots'] as List).cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> grantRoot(String path, {bool trusted = false}) =>
      _request('POST', '/roots', body: {'action': 'grant', 'path': path, 'trusted': trusted});

  Future<void> revokeRoot(String path) =>
      _request('POST', '/roots', body: {'action': 'revoke', 'path': path});

  Future<void> setTrusted(String path, bool trusted) => _request(
        'POST',
        '/roots',
        body: {'action': trusted ? 'trust' : 'untrust', 'path': path},
      );

  Future<Map<String, dynamic>> listDirectory({required String root, String path = ''}) => _request(
        'GET',
        '/list?root=${Uri.encodeQueryComponent(root)}&path=${Uri.encodeQueryComponent(path)}',
      );

  Future<Map<String, dynamic>> readFile({required String root, required String path}) => _request(
        'GET',
        '/read?root=${Uri.encodeQueryComponent(root)}&path=${Uri.encodeQueryComponent(path)}',
      );

  /// D5's interactive consent queue (`aura_hostd/lib/src/approval_queue.dart`) -- lists every
  /// currently-pending approval. `command` in each entry is already the redacted display string;
  /// the daemon never serializes the real command it will execute.
  Future<List<Map<String, dynamic>>> listApprovals() async {
    final result = await _request('GET', '/approvals');
    return (result['approvals'] as List).cast<Map<String, dynamic>>();
  }

  Future<void> respondToApproval(String id, bool approved) =>
      _request('POST', '/approvals/$id/respond', body: {'approved': approved});

  /// D5's Source Control panel routes (`aura_hostd/lib/src/git_service.dart`) -- direct,
  /// human-only local git execution, entirely independent of the agent-facing RPC surface.
  Future<Map<String, dynamic>> gitStatus({required String root}) =>
      _request('GET', '/git/status?root=${Uri.encodeQueryComponent(root)}');

  Future<Map<String, dynamic>> gitDiff({required String root, String? file, bool staged = false}) {
    final query = StringBuffer('root=${Uri.encodeQueryComponent(root)}&staged=$staged');
    if (file != null) query.write('&file=${Uri.encodeQueryComponent(file)}');
    return _request('GET', '/git/diff?$query');
  }

  Future<Map<String, dynamic>> gitLog({required String root, int limit = 30}) =>
      _request('GET', '/git/log?root=${Uri.encodeQueryComponent(root)}&limit=$limit');

  Future<Map<String, dynamic>> gitStage({required String root, required List<String> paths}) =>
      _request('POST', '/git/stage', body: {'root': root, 'paths': paths});

  Future<Map<String, dynamic>> gitUnstage({required String root, required List<String> paths}) =>
      _request('POST', '/git/unstage', body: {'root': root, 'paths': paths});

  Future<Map<String, dynamic>> gitCommit({required String root, required String message}) =>
      _request('POST', '/git/commit', body: {'root': root, 'message': message});

  Future<Map<String, dynamic>> gitCheckout({required String root, required String branch, bool create = false}) =>
      _request('POST', '/git/checkout', body: {'root': root, 'branch': branch, 'create': create});

  /// D5 Git panel: merges [branch] into the human's own checked-out working tree -- distinct from
  /// [gitMergeTask] below, which only ever targets a task's isolated worktree.
  Future<Map<String, dynamic>> gitMerge({required String root, required String branch}) =>
      _request('POST', '/git/merge', body: {'root': root, 'branch': branch});

  Future<Map<String, dynamic>> gitMergeAbort({required String root}) =>
      _request('POST', '/git/merge-abort', body: {'root': root});

  /// Human-only remote-sync routes (`git_service.dart` branches/fetch/pull/push) --
  /// same boundary as the git routes above. `push` takes no refspec server-side:
  /// only the checked-out branch can ever go to its own upstream.
  Future<Map<String, dynamic>> gitBranches({required String root}) =>
      _request('GET', '/git/branches?root=${Uri.encodeQueryComponent(root)}');

  Future<Map<String, dynamic>> gitFetch({required String root}) =>
      _request('POST', '/git/fetch', body: {'root': root});

  Future<Map<String, dynamic>> gitPull({required String root}) =>
      _request('POST', '/git/pull', body: {'root': root});

  Future<Map<String, dynamic>> gitPush({required String root}) =>
      _request('POST', '/git/push', body: {'root': root});

  /// D7 Part A.2's diagnostics routes (`aura_hostd/lib/src/diagnostics_service.dart`) -- direct,
  /// human-only batch linting, entirely independent of the agent-facing RPC surface (same
  /// boundary as the git routes above).
  Future<Map<String, dynamic>> diagnosticsDetect({required String root}) =>
      _request('GET', '/diagnostics/detect?root=${Uri.encodeQueryComponent(root)}');

  Future<Map<String, dynamic>> diagnosticsRun({required String root, required String tool}) =>
      _request('POST', '/diagnostics/run', body: {'root': root, 'tool': tool});

  /// D9's "Agent Review" panel routes (`aura_hostd/lib/src/control_server.dart` +
  /// `isolated_task_registry.dart`) -- every isolated per-task worktree this daemon currently
  /// knows about, and the human-only review/merge/discard actions on them. Entirely independent
  /// of the agent-facing RPC surface, same boundary as the git routes above.
  Future<List<Map<String, dynamic>>> tasksList() async {
    final result = await _request('GET', '/tasks');
    return (result['tasks'] as List).cast<Map<String, dynamic>>();
  }

  Future<Map<String, dynamic>> gitTaskDiff({required String taskKey, required String base}) => _request(
        'GET',
        '/git/task-diff?task_key=${Uri.encodeQueryComponent(taskKey)}&base=${Uri.encodeQueryComponent(base)}',
      );

  Future<Map<String, dynamic>> gitTaskDiffStat({required String taskKey, required String base}) => _request(
        'GET',
        '/git/task-diff-stat?task_key=${Uri.encodeQueryComponent(taskKey)}&base=${Uri.encodeQueryComponent(base)}',
      );

  Future<Map<String, dynamic>> gitMergeTask({
    required String root,
    required String taskBranch,
    bool squash = false,
  }) =>
      _request('POST', '/git/merge-task', body: {'root': root, 'task_branch': taskBranch, 'squash': squash});

  Future<Map<String, dynamic>> gitDiscardTask({required String taskKey}) =>
      _request('POST', '/git/discard-task', body: {'task_key': taskKey});

  Future<Map<String, dynamic>> startPairing({String? backendUrl}) =>
      _request('POST', '/pair/start', body: {'backend_url': ?backendUrl});

  Future<Map<String, dynamic>> pairingStatus() => _request('GET', '/pair/status');

  Future<void> shutdown() => _request('POST', '/shutdown');

  void close() => _http.close();
}
