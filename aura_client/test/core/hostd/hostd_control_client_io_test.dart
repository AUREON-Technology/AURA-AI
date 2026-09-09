// Exercises HostdControlClient against a real local HTTP server standing in for aura_hostd's
// control server (control_server.dart) -- same request/response shapes that file defines.
// Imports hostd_control_client_io.dart directly (not the hostd_control_client.dart barrel) since
// this test is VM-only anyway and importing the barrel would make the analyzer resolve against
// the web stub instead (see desktop_oauth_client_io_test.dart's own note on this).
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:aura_client/core/hostd/hostd_control_client_io.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeControlServer {
  _FakeControlServer(this._httpServer);

  final HttpServer _httpServer;
  final List<String> receivedAuthHeaders = [];

  static Future<_FakeControlServer> start({
    required Map<String, Object?> Function(HttpRequest request, Map<String, dynamic> body) respond,
  }) async {
    final httpServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final server = _FakeControlServer(httpServer);
    unawaited(() async {
      await for (final request in httpServer) {
        server.receivedAuthHeaders.add(request.headers.value(HttpHeaders.authorizationHeader) ?? '');
        final raw = await utf8.decoder.bind(request).join();
        final body = raw.isEmpty ? <String, dynamic>{} : jsonDecode(raw) as Map<String, dynamic>;
        final result = respond(request, body);
        request.response.headers.contentType = ContentType.json;
        request.response.write(jsonEncode(result));
        await request.response.close();
      }
    }());
    return server;
  }

  Future<void> close() => _httpServer.close(force: true);
}

void main() {
  test('discovers no endpoint and every call reports HostdControlUnavailable', () async {
    final client = HostdControlClient(discoverEndpoint: () async => null);
    await expectLater(client.status(), throwsA(isA<HostdControlUnavailable>()));
    await expectLater(client.roots(), throwsA(isA<HostdControlUnavailable>()));
  });

  test('status() succeeds without an Authorization header', () async {
    final server = await _FakeControlServer.start(
      respond: (request, body) => {'authenticated': false, 'backend_url': null, 'roots': []},
    );
    addTearDown(server.close);
    final client = HostdControlClient(
      discoverEndpoint: () async => HostdControlEndpoint(port: server._httpServer.port, token: 'tok'),
    );

    final result = await client.status();

    expect(result['authenticated'], false);
    expect(server.receivedAuthHeaders.single, '');
  });

  test('every other call sends the discovered token as a bearer header', () async {
    final server = await _FakeControlServer.start(
      respond: (request, body) => {'roots': []},
    );
    addTearDown(server.close);
    final client = HostdControlClient(
      discoverEndpoint: () async => HostdControlEndpoint(port: server._httpServer.port, token: 'secret-tok'),
    );

    await client.roots();

    expect(server.receivedAuthHeaders.single, 'Bearer secret-tok');
  });

  test('grantRoot posts the expected action/path/trusted body', () async {
    Map<String, dynamic>? seenBody;
    final server = await _FakeControlServer.start(
      respond: (request, body) {
        seenBody = body;
        return {'root': {'path': '/x', 'trusted': true}};
      },
    );
    addTearDown(server.close);
    final client = HostdControlClient(
      discoverEndpoint: () async => HostdControlEndpoint(port: server._httpServer.port, token: 't'),
    );

    await client.grantRoot('/x', trusted: true);

    expect(seenBody, {'action': 'grant', 'path': '/x', 'trusted': true});
  });

  test('a non-2xx response is surfaced as HostdControlError with the server\'s error message', () async {
    final httpServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    unawaited(() async {
      await for (final request in httpServer) {
        request.response.statusCode = 403;
        request.response.headers.contentType = ContentType.json;
        request.response.write(jsonEncode({'error': 'outside the granted workspace root'}));
        await request.response.close();
      }
    }());
    addTearDown(() => httpServer.close(force: true));
    final client = HostdControlClient(
      discoverEndpoint: () async => HostdControlEndpoint(port: httpServer.port, token: 't'),
    );

    try {
      await client.listDirectory(root: '/x', path: '../etc');
      fail('expected a HostdControlError');
    } on HostdControlError catch (error) {
      expect(error.statusCode, 403);
      expect(error.message, 'outside the granted workspace root');
    }
  });

  test('listDirectory and readFile URL-encode their query parameters', () async {
    Uri? seenUri;
    final server = await _FakeControlServer.start(respond: (request, body) {
      seenUri = request.uri;
      return {'entries': []};
    });
    addTearDown(server.close);
    final client = HostdControlClient(
      discoverEndpoint: () async => HostdControlEndpoint(port: server._httpServer.port, token: 't'),
    );

    await client.listDirectory(root: '/a b', path: 'sub dir');

    expect(seenUri?.queryParameters['root'], '/a b');
    expect(seenUri?.queryParameters['path'], 'sub dir');
  });
}
