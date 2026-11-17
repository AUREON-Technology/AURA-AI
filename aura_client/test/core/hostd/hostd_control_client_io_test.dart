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

  test('status() sends the token, since the daemon only lists roots to an authorized caller '
      '(INT-037)', () async {
    final server = await _FakeControlServer.start(
      respond: (request, body) => {'authenticated': false, 'backend_url': null, 'roots': []},
    );
    addTearDown(server.close);
    final client = HostdControlClient(
      discoverEndpoint: () async => HostdControlEndpoint(port: server._httpServer.port, token: 'tok'),
    );

    final result = await client.status();

    expect(result['authenticated'], false);
    expect(server.receivedAuthHeaders.single, 'Bearer tok');
  });

  test('a daemon that accepts the connection but never answers is reported unavailable after '
      'the timeout, not awaited forever (INT-038)', () async {
    final httpServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final held = <HttpRequest>[];
    httpServer.listen(held.add); // never responds
    addTearDown(() => httpServer.close(force: true));
    final client = HostdControlClient(
      discoverEndpoint: () async => HostdControlEndpoint(port: httpServer.port, token: 'tok'),
      requestTimeout: const Duration(milliseconds: 200),
    );
    addTearDown(client.close);

    await expectLater(
      client.pairingStatus(),
      throwsA(isA<HostdControlUnavailable>().having((e) => e.message, 'message', contains('did not answer'))),
    );
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

  test('a stale-daemon "no such endpoint" 404 is enriched with a rebuild hint', () async {
    final httpServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    unawaited(() async {
      await for (final request in httpServer) {
        request.response.statusCode = 404;
        request.response.headers.contentType = ContentType.json;
        request.response.write(jsonEncode({'error': 'no such endpoint'}));
        await request.response.close();
      }
    }());
    addTearDown(() => httpServer.close(force: true));
    final client = HostdControlClient(
      discoverEndpoint: () async => HostdControlEndpoint(port: httpServer.port, token: 't'),
    );

    try {
      await client.roots();
      fail('expected a HostdControlError');
    } on HostdControlError catch (error) {
      expect(error.statusCode, 404);
      expect(error.message, contains('out of date'));
      expect(error.message, contains('Rebuild and restart'));
    }
  });

  test('a real "not a granted root" 404 keeps its own message unchanged', () async {
    final httpServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    unawaited(() async {
      await for (final request in httpServer) {
        request.response.statusCode = 404;
        request.response.headers.contentType = ContentType.json;
        request.response.write(jsonEncode({'error': '/x is not a granted root'}));
        await request.response.close();
      }
    }());
    addTearDown(() => httpServer.close(force: true));
    final client = HostdControlClient(
      discoverEndpoint: () async => HostdControlEndpoint(port: httpServer.port, token: 't'),
    );

    try {
      await client.roots();
      fail('expected a HostdControlError');
    } on HostdControlError catch (error) {
      expect(error.statusCode, 404);
      expect(error.message, '/x is not a granted root');
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

  group('remote control (WP8, D-10b)', () {
    test('setRemoteControl POSTs {enabled} to /remote_control with the token and returns the '
        "daemon's answer", () async {
      final seen = <String>[];
      Map<String, dynamic>? seenBody;
      final server = await _FakeControlServer.start(
        respond: (request, body) {
          seen.add('${request.method} ${request.uri.path}');
          seenBody = body;
          return {'remote_control': body['enabled']};
        },
      );
      addTearDown(server.close);
      final client = HostdControlClient(
        discoverEndpoint: () async => HostdControlEndpoint(port: server._httpServer.port, token: 'tok'),
      );

      expect(await client.setRemoteControl(true), isTrue);
      expect(seen.single, 'POST /remote_control');
      expect(seenBody, {'enabled': true});
      expect(server.receivedAuthHeaders.single, 'Bearer tok');

      expect(await client.setRemoteControl(false), isFalse);
      expect(seenBody, {'enabled': false});
    });

    test('the answer is what the daemon says, not what was asked for', () async {
      final server = await _FakeControlServer.start(respond: (request, body) => {'remote_control': false});
      addTearDown(server.close);
      final client = HostdControlClient(
        discoverEndpoint: () async => HostdControlEndpoint(port: server._httpServer.port, token: 't'),
      );

      expect(await client.setRemoteControl(true), isFalse);
    });

    test('an older daemon without the route reports the out-of-date hint', () async {
      final httpServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      unawaited(() async {
        await for (final request in httpServer) {
          request.response.statusCode = 404;
          request.response.headers.contentType = ContentType.json;
          request.response.write(jsonEncode({'error': 'no such endpoint'}));
          await request.response.close();
        }
      }());
      addTearDown(() => httpServer.close(force: true));
      final client = HostdControlClient(
        discoverEndpoint: () async => HostdControlEndpoint(port: httpServer.port, token: 't'),
      );

      await expectLater(
        client.setRemoteControl(true),
        throwsA(isA<HostdControlError>().having((e) => e.message, 'message', contains('out of date'))),
      );
    });
  });
}
