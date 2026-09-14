/// HTTP client for `aura_hostd`'s local control server (`aura_hostd/lib/src/control_server.dart`,
/// ADR-011 Unit 8) -- a *separate process* on the same machine. This never talks to the AURA
/// backend; every call here is a loopback request to whatever port/token `control.json` names,
/// discovered fresh on every call rather than cached, since the daemon can be started, stopped,
/// or restarted (picking a new ephemeral port) independently of this app's own lifecycle.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

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
      throw HostdControlUnavailable('aura_hostd is not running.');
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
      throw HostdControlUnavailable('Could not reach aura_hostd: $error');
    } on SocketException catch (error) {
      throw HostdControlUnavailable('Could not reach aura_hostd: $error');
    }
    final decoded = response.body.isEmpty ? <String, dynamic>{} : jsonDecode(response.body);
    final decodedMap = decoded is Map<String, dynamic> ? decoded : <String, dynamic>{};
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw HostdControlError(
        response.statusCode,
        (decodedMap['error'] as String?) ?? 'HTTP ${response.statusCode}',
      );
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

  /// D7 Part A.2's diagnostics routes (`aura_hostd/lib/src/diagnostics_service.dart`) -- direct,
  /// human-only batch linting, entirely independent of the agent-facing RPC surface (same
  /// boundary as the git routes above).
  Future<Map<String, dynamic>> diagnosticsDetect({required String root}) =>
      _request('GET', '/diagnostics/detect?root=${Uri.encodeQueryComponent(root)}');

  Future<Map<String, dynamic>> diagnosticsRun({required String root, required String tool}) =>
      _request('POST', '/diagnostics/run', body: {'root': root, 'tool': tool});

  Future<Map<String, dynamic>> startPairing({String? backendUrl}) =>
      _request('POST', '/pair/start', body: {'backend_url': ?backendUrl});

  Future<Map<String, dynamic>> pairingStatus() => _request('GET', '/pair/status');

  Future<void> shutdown() => _request('POST', '/shutdown');

  void close() => _http.close();
}
