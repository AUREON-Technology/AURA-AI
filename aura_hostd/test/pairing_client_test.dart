// Exercises pairWithCode against a real local HTTP server -- no mocking of dart:io -- covering
// the exact request/response shapes api/v2/auth.py's /desktop/pair/{start,poll} endpoints use.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:aura_hostd/src/pairing_client.dart';
import 'package:test/test.dart';

typedef _Responder = Future<Map<String, Object?>> Function(Map<String, dynamic> body);

class _FakeAuthServer {
  _FakeAuthServer(this._httpServer);

  final HttpServer _httpServer;
  final List<String> requestedPaths = [];
  final Map<String, _Responder> _handlers = {};

  static Future<_FakeAuthServer> start() async {
    final httpServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final server = _FakeAuthServer(httpServer);
    unawaited(server._serve());
    return server;
  }

  String get backendUrl => 'http://127.0.0.1:${_httpServer.port}';

  void on(String path, _Responder responder) => _handlers[path] = responder;

  Future<void> _serve() async {
    await for (final request in _httpServer) {
      requestedPaths.add(request.uri.path);
      final raw = await utf8.decoder.bind(request).join();
      final body = raw.isEmpty ? <String, dynamic>{} : jsonDecode(raw) as Map<String, dynamic>;
      final handler = _handlers[request.uri.path];
      if (handler == null) {
        request.response.statusCode = 404;
        await request.response.close();
        continue;
      }
      final result = await handler(body);
      request.response.headers.contentType = ContentType.json;
      request.response.write(jsonEncode(result));
      await request.response.close();
    }
  }

  Future<void> close() => _httpServer.close(force: true);
}

void main() {
  late _FakeAuthServer server;

  setUp(() async {
    server = await _FakeAuthServer.start();
  });

  tearDown(() async {
    await server.close();
  });

  test('happy path: start, one pending poll, then approved', () async {
    var pollCount = 0;
    server.on('/api/v3/auth/desktop/pair/start', (body) async => {
          'pairing_id': 'pid-1',
          'user_code': 'AB3D-9XYZ',
          'expires_in': 60,
          'interval': 0, // no real delay in the test
        });
    server.on('/api/v3/auth/desktop/pair/poll', (body) async {
      expect(body['pairing_id'], 'pid-1');
      pollCount++;
      if (pollCount == 1) return {'status': 'pending'};
      return {'status': 'approved', 'access_token': 'tok-abc', 'device_id': 'dev-1'};
    });

    String? shownCode;
    final result = await pairWithCode(
      server.backendUrl,
      onCode: (code) => shownCode = code,
    );

    expect(shownCode, 'AB3D-9XYZ');
    expect(result.accessToken, 'tok-abc');
    expect(result.deviceId, 'dev-1');
    expect(pollCount, 2);
  });

  test('throws PairingFailedError when the session expires before approval', () async {
    server.on('/api/v3/auth/desktop/pair/start', (body) async => {
          'pairing_id': 'pid-2',
          'user_code': 'ZZZZ-ZZZZ',
          'expires_in': 0, // already expired -- the poll loop must not spin
          'interval': 0,
        });
    server.on('/api/v3/auth/desktop/pair/poll', (body) async => {'status': 'pending'});

    await expectLater(
      pairWithCode(server.backendUrl, onCode: (_) {}),
      throwsA(isA<PairingFailedError>()),
    );
  });

  test('throws PairingFailedError when the poll reports expired', () async {
    server.on('/api/v3/auth/desktop/pair/start', (body) async => {
          'pairing_id': 'pid-3',
          'user_code': 'CCCC-DDDD',
          'expires_in': 60,
          'interval': 0,
        });
    server.on('/api/v3/auth/desktop/pair/poll', (body) async => {'status': 'expired'});

    await expectLater(
      pairWithCode(server.backendUrl, onCode: (_) {}),
      throwsA(isA<PairingFailedError>()),
    );
  });

  test('throws PairingFailedError on a non-2xx response', () async {
    // A separate raw server (not _FakeAuthServer, which always answers 200) so the /start call
    // itself fails -- exercising _postJson's status-code check directly.
    final errorServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    unawaited(() async {
      await for (final request in errorServer) {
        request.response.statusCode = 500;
        request.response.write('{"detail": "boom"}');
        await request.response.close();
      }
    }());
    addTearDown(() => errorServer.close(force: true));

    await expectLater(
      pairWithCode('http://127.0.0.1:${errorServer.port}', onCode: (_) {}),
      throwsA(isA<PairingFailedError>()),
    );
  });
}
