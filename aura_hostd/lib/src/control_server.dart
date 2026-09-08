/// Loopback-only HTTP control plane for the Flutter desktop UI (Unit 3/8).
///
/// Bound exclusively to `127.0.0.1` so a confused or compromised backend cannot reach it.
/// The UI supervises grants / YOLO / status without going through the host-agent WebSocket.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'config.dart';
import 'containment.dart';
import 'task_handler.dart';
import 'workspace_store.dart';

/// Default control port. Overridable via `AURA_HOSTD_CONTROL_PORT`.
const int defaultControlPort = 8765;

class ControlServer {
  ControlServer({
    required this.configStore,
    required this.workspaceStore,
    required this.roots,
    required this.handler,
    required this.isConnected,
    this.port = defaultControlPort,
  });

  final ConfigStore configStore;
  final WorkspaceStore workspaceStore;
  final WorkspaceRoots roots;
  final TaskHandler handler;
  final bool Function() isConnected;
  final int port;

  HttpServer? _server;

  Future<void> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, port);
    unawaited(_serve());
  }

  Future<void> stop() async {
    await _server?.close(force: true);
    _server = null;
  }

  Future<void> _serve() async {
    final server = _server;
    if (server == null) return;
    await for (final request in server) {
      try {
        await _handle(request);
      } catch (error) {
        request.response.statusCode = 500;
        request.response.write(jsonEncode({'error': error.toString()}));
        await request.response.close();
      }
    }
  }

  Future<void> _handle(HttpRequest request) async {
    final path = request.uri.path;
    if (request.method == 'GET' && path == '/status') {
      final config = configStore.load();
      _json(request, {
        'connected': isConnected(),
        'yolo': config.yolo,
        'backend_url': config.backendUrl,
        'has_auth_token': config.authToken != null && config.authToken!.isNotEmpty,
        'roots': [
          for (final root in roots.all)
            {'path': root.canonicalPath, 'trusted': root.trusted},
        ],
      });
      return;
    }
    if (request.method == 'GET' && path == '/roots') {
      _json(request, {
        'roots': [
          for (final root in roots.all)
            {'path': root.canonicalPath, 'trusted': root.trusted},
        ],
      });
      return;
    }
    if (request.method == 'POST' && path == '/grant') {
      final body = await _readJson(request);
      final pathArg = body['path'] as String?;
      if (pathArg == null || pathArg.isEmpty) {
        _error(request, 400, 'path required');
        return;
      }
      final trusted = body['trusted'] == true;
      try {
        final root = roots.addRoot(pathArg, trusted: trusted);
        workspaceStore.save(roots);
        _json(request, {'path': root.canonicalPath, 'trusted': root.trusted});
      } on InvalidRootError catch (error) {
        _error(request, 400, error.toString());
      }
      return;
    }
    if (request.method == 'POST' && path == '/revoke') {
      final body = await _readJson(request);
      final pathArg = body['path'] as String?;
      if (pathArg == null) {
        _error(request, 400, 'path required');
        return;
      }
      final removed = roots.removeRoot(pathArg);
      if (removed) workspaceStore.save(roots);
      _json(request, {'removed': removed});
      return;
    }
    if (request.method == 'POST' && (path == '/trust' || path == '/untrust')) {
      final body = await _readJson(request);
      final pathArg = body['path'] as String?;
      if (pathArg == null) {
        _error(request, 400, 'path required');
        return;
      }
      final updated = roots.setTrusted(pathArg, path == '/trust');
      if (updated == null) {
        _error(request, 404, 'not a granted root');
        return;
      }
      workspaceStore.save(roots);
      _json(request, {'path': updated.canonicalPath, 'trusted': updated.trusted});
      return;
    }
    if (request.method == 'POST' && path == '/yolo') {
      final body = await _readJson(request);
      final enabled = body['enabled'] == true;
      final config = configStore.load().copyWith(yolo: enabled);
      configStore.save(config);
      handler.yolo = enabled;
      _json(request, {'yolo': enabled});
      return;
    }
    _error(request, 404, 'not found');
  }

  Future<Map<String, dynamic>> _readJson(HttpRequest request) async {
    final raw = await utf8.decoder.bind(request).join();
    if (raw.trim().isEmpty) return {};
    return jsonDecode(raw) as Map<String, dynamic>;
  }

  void _json(HttpRequest request, Map<String, Object?> body) {
    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode(body));
    request.response.close();
  }

  void _error(HttpRequest request, int status, String message) {
    request.response.statusCode = status;
    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode({'error': message}));
    request.response.close();
  }
}
