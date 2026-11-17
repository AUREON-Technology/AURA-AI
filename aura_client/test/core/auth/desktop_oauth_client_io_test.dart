// Exercises signInWithGoogleLoopback's full RFC 8252 loopback flow against a local fake HTTP
// server standing in for Google's token endpoint, and a fake `launch` callback standing in for
// the system browser (it plays "the browser" by making the exact redirect request a real one
// would, reading the state/redirect_uri Google would have echoed back).
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:aura_client/core/auth/desktop_oauth_client.dart';
import 'package:flutter_test/flutter_test.dart';

class _FakeTokenServer {
  _FakeTokenServer(this._httpServer);

  final HttpServer _httpServer;
  Map<String, dynamic>? lastRequestBody;

  static Future<_FakeTokenServer> start({required Map<String, Object?> response, int statusCode = 200}) async {
    final httpServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final server = _FakeTokenServer(httpServer);
    unawaited(() async {
      await for (final request in httpServer) {
        final raw = await utf8.decoder.bind(request).join();
        server.lastRequestBody = Uri.splitQueryString(raw);
        request.response.statusCode = statusCode;
        request.response.headers.contentType = ContentType.json;
        request.response.write(jsonEncode(response));
        await request.response.close();
      }
    }());
    return server;
  }

  Uri get uri => Uri.parse('http://127.0.0.1:${_httpServer.port}/token');

  Future<void> close() => _httpServer.close(force: true);
}

/// Stands in for the AURA backend's `POST /api/v3/auth/desktop/exchange`: records the JSON the
/// app sent and answers with [response].
class _FakeBackend {
  _FakeBackend(this._httpServer);

  final HttpServer _httpServer;
  Map<String, dynamic>? lastRequestBody;
  String? lastContentType;
  int requestCount = 0;

  static Future<_FakeBackend> start({required Object? response, int statusCode = 200}) async {
    final httpServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final server = _FakeBackend(httpServer);
    unawaited(() async {
      await for (final request in httpServer) {
        server.requestCount++;
        server.lastContentType = request.headers.contentType?.mimeType;
        final raw = await utf8.decoder.bind(request).join();
        server.lastRequestBody = jsonDecode(raw) as Map<String, dynamic>;
        request.response.statusCode = statusCode;
        request.response.headers.contentType = ContentType.json;
        request.response.write(response is String ? response : jsonEncode(response));
        await request.response.close();
      }
    }());
    return server;
  }

  Uri get uri => Uri.parse('http://127.0.0.1:${_httpServer.port}/api/v3/auth/desktop/exchange');

  Future<void> close() => _httpServer.close(force: true);
}

/// Simulates the browser completing the OAuth dance: hits the loopback redirect_uri from
/// [authUri] with the given query params, exactly as Google would after the user approves.
Future<void> _hitRedirect(Uri authUri, Map<String, String> extraParams) async {
  final redirectUri = Uri.parse(authUri.queryParameters['redirect_uri']!);
  final state = authUri.queryParameters['state']!;
  final callback = redirectUri.replace(queryParameters: {'state': state, ...extraParams});
  final client = HttpClient();
  try {
    final request = await client.getUrl(callback);
    final response = await request.close();
    await response.drain<void>();
  } finally {
    client.close(force: true);
  }
}

/// A fake `launch` for [signInWithGoogleLoopback] that hits the redirect with [extraParams].
///
/// Critically, it does **not** await that request before returning -- a real browser launch
/// returns as soon as the OS hands off to the browser, well before any navigation happens, and
/// `signInWithGoogleLoopback` doesn't start listening for the redirect (`server.first`) until
/// *after* `launch` returns. Awaiting the full round trip here would deadlock: the simulated
/// "browser" would be stuck waiting for a response from a server that has no listener yet.
Future<bool> Function(Uri authUri) _fakeLaunch(Map<String, String> extraParams) {
  return (authUri) {
    unawaited(_hitRedirect(authUri, extraParams));
    return Future<bool>.value(true);
  };
}

void main() {
  test('happy path returns the id_token from the fake token endpoint', () async {
    final tokenServer = await _FakeTokenServer.start(
      response: {'id_token': 'fake-id-token', 'access_token': 'fake-access'},
    );
    addTearDown(tokenServer.close);

    final result = await signInWithGoogleLoopback(
      clientId: 'test-client-id',
      tokenEndpoint: tokenServer.uri,
      launch: _fakeLaunch({'code': 'auth-code-123'}),
    );

    expect(result.idToken, 'fake-id-token');
    expect(tokenServer.lastRequestBody?['client_id'], 'test-client-id');
    expect(tokenServer.lastRequestBody?['code'], 'auth-code-123');
    expect(tokenServer.lastRequestBody?['grant_type'], 'authorization_code');
    expect(tokenServer.lastRequestBody?['code_verifier'], isNotEmpty);
  });

  // The whole point of a public "Desktop app" client (RFC 8252 §8.5): the code_verifier is the
  // only proof and no client_secret is sent. Asserting *absent* rather than empty matters --
  // Google rejects a present-but-blank client_secret the same way it rejects a wrong one, so a
  // regression that sent '' would still look like "omitting the secret" while failing live.
  test('omits client_secret entirely when none is configured', () async {
    final tokenServer = await _FakeTokenServer.start(
      response: {'id_token': 'fake-id-token'},
    );
    addTearDown(tokenServer.close);

    await signInWithGoogleLoopback(
      clientId: 'test-client-id',
      tokenEndpoint: tokenServer.uri,
      launch: _fakeLaunch({'code': 'auth-code-123'}),
    );

    expect(tokenServer.lastRequestBody, isNot(contains('client_secret')));
    expect(tokenServer.lastRequestBody?['code_verifier'], isNotEmpty);
  });

  // The escape hatch: a build pointed at a confidential ("Web application") client can still
  // inject a secret, and it has to reach the exchange when it does.
  test('sends client_secret only when one is explicitly configured', () async {
    final tokenServer = await _FakeTokenServer.start(
      response: {'id_token': 'fake-id-token'},
    );
    addTearDown(tokenServer.close);

    await signInWithGoogleLoopback(
      clientId: 'test-client-id',
      clientSecret: 'test-client-secret',
      tokenEndpoint: tokenServer.uri,
      launch: _fakeLaunch({'code': 'auth-code-123'}),
    );

    expect(tokenServer.lastRequestBody?['client_secret'], 'test-client-secret');
  });

  // This used to assert a message blaming "a wrong-typed OAuth client". That diagnosis was wrong:
  // the registered client is type Desktop in Google Cloud Console and Google still answers a
  // secret-less exchange with "client_secret is missing" (2026-11-17), so following the old
  // advice fixed nothing and sign-in kept failing. The message now says what is actually true --
  // a direct-to-Google build needs a secret, and the normal build avoids that via the backend.
  test('says a direct exchange needs a secret, and points at the backend route', () async {
    final tokenServer = await _FakeTokenServer.start(
      response: {'error': 'invalid_request', 'error_description': 'client_secret is missing.'},
      statusCode: 400,
    );
    addTearDown(tokenServer.close);

    await expectLater(
      signInWithGoogleLoopback(
        clientId: 'some-client-id',
        tokenEndpoint: tokenServer.uri,
        launch: _fakeLaunch({'code': 'auth-code-123'}),
      ),
      throwsA(
        isA<DesktopOAuthCancelledException>().having(
          (e) => e.message,
          'message',
          allOf(
            contains('client secret'),
            contains('some-client-id'),
            contains('/api/v3/auth/desktop/exchange'),
            isNot(contains('wrong type')),
            isNot(contains('not registered as')),
          ),
        ),
      ),
    );
  });

  group('exchange through the AURA backend', () {
    // The fix for the live failure: Google demands the client secret, this binary must not carry
    // it, so the app sends the code and its PKCE proof to the server, which adds the secret.
    test('sends the code, verifier and loopback redirect as JSON and returns the id_token', () async {
      final backend = await _FakeBackend.start(response: {'id_token': 'backend-id-token'});
      addTearDown(backend.close);
      final google = await _FakeTokenServer.start(response: {'id_token': 'must-not-be-used'});
      addTearDown(google.close);

      final result = await signInWithGoogleLoopback(
        clientId: 'test-client-id',
        backendExchangeEndpoint: backend.uri,
        tokenEndpoint: google.uri,
        launch: _fakeLaunch({'code': 'auth-code-123'}),
      );

      expect(result.idToken, 'backend-id-token');
      expect(backend.lastContentType, 'application/json');
      expect(backend.lastRequestBody?['code'], 'auth-code-123');
      expect(backend.lastRequestBody?['code_verifier'], hasLength(greaterThanOrEqualTo(43)));
      expect(
        backend.lastRequestBody?['redirect_uri'],
        matches(RegExp(r'^http://127\.0\.0\.1:\d+/$')),
      );
      expect(backend.lastRequestBody, isNot(contains('client_secret')));
      expect(google.lastRequestBody, isNull, reason: 'the app must not call Google itself');
    });

    // The PKCE verifier has to satisfy the server's own bounds (RFC 7636: 43-128 unreserved
    // characters) or every real sign-in is a 422 the user can do nothing about.
    test('the verifier it sends fits RFC 7636', () async {
      final backend = await _FakeBackend.start(response: {'id_token': 'backend-id-token'});
      addTearDown(backend.close);

      await signInWithGoogleLoopback(
        clientId: 'test-client-id',
        backendExchangeEndpoint: backend.uri,
        launch: _fakeLaunch({'code': 'auth-code-123'}),
      );

      expect(
        backend.lastRequestBody?['code_verifier'],
        matches(RegExp(r'^[A-Za-z0-9\-._~]{43,128}$')),
      );
    });

    test('surfaces the server\'s own reason when it refuses', () async {
      final backend = await _FakeBackend.start(
        response: {'detail': 'Desktop sign-in is not configured on this server.'},
        statusCode: 503,
      );
      addTearDown(backend.close);

      await expectLater(
        signInWithGoogleLoopback(
          clientId: 'test-client-id',
          backendExchangeEndpoint: backend.uri,
          launch: _fakeLaunch({'code': 'auth-code-123'}),
        ),
        throwsA(
          isA<DesktopOAuthCancelledException>().having(
            (e) => e.message,
            'message',
            'Desktop sign-in is not configured on this server.',
          ),
        ),
      );
    });

    test('falls back to the HTTP status when the error body has no detail', () async {
      final backend = await _FakeBackend.start(response: '<html>bad gateway</html>', statusCode: 502);
      addTearDown(backend.close);

      await expectLater(
        signInWithGoogleLoopback(
          clientId: 'test-client-id',
          backendExchangeEndpoint: backend.uri,
          launch: _fakeLaunch({'code': 'auth-code-123'}),
        ),
        throwsA(
          isA<DesktopOAuthCancelledException>().having(
            (e) => e.message,
            'message',
            allOf(contains('502'), isNot(contains('<html>'))),
          ),
        ),
      );
    });

    test('rejects a 200 that carries no id_token', () async {
      final backend = await _FakeBackend.start(response: {'access_token': 'only-access'});
      addTearDown(backend.close);

      await expectLater(
        signInWithGoogleLoopback(
          clientId: 'test-client-id',
          backendExchangeEndpoint: backend.uri,
          launch: _fakeLaunch({'code': 'auth-code-123'}),
        ),
        throwsA(isA<DesktopOAuthCancelledException>()),
      );
    });

    // Anything that is not a DesktopOAuthCancelledException would fall through to the generic
    // "could not classify" screen -- the exact symptom this work started from.
    test('an unreachable backend is a sign-in failure, not an uncaught socket error', () async {
      final closed = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final deadUri = Uri.parse('http://127.0.0.1:${closed.port}/api/v3/auth/desktop/exchange');
      await closed.close(force: true);

      await expectLater(
        signInWithGoogleLoopback(
          clientId: 'test-client-id',
          backendExchangeEndpoint: deadUri,
          launch: _fakeLaunch({'code': 'auth-code-123'}),
        ),
        throwsA(
          isA<DesktopOAuthCancelledException>().having(
            (e) => e.message,
            'message',
            contains('Could not reach the AURA server'),
          ),
        ),
      );
    });

    // The escape hatch keeps its meaning: a build that injects a secret talks to Google itself.
    test('an injected client secret takes precedence and bypasses the backend', () async {
      final backend = await _FakeBackend.start(response: {'id_token': 'backend-id-token'});
      addTearDown(backend.close);
      final google = await _FakeTokenServer.start(response: {'id_token': 'direct-id-token'});
      addTearDown(google.close);

      final result = await signInWithGoogleLoopback(
        clientId: 'test-client-id',
        clientSecret: 'test-client-secret',
        backendExchangeEndpoint: backend.uri,
        tokenEndpoint: google.uri,
        launch: _fakeLaunch({'code': 'auth-code-123'}),
      );

      expect(result.idToken, 'direct-id-token');
      expect(google.lastRequestBody?['client_secret'], 'test-client-secret');
      expect(backend.requestCount, 0);
    });
  });

  test('throws immediately when no client id is configured', () async {
    await expectLater(
      signInWithGoogleLoopback(
        clientId: '',
        launch: (_) async => true,
      ),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });

  // Deliberately no "refuses without a client secret" case any more: an empty secret is the
  // normal configuration for a public Desktop-app client, covered by the omission test above.

  test('throws when the browser cannot be opened', () async {
    await expectLater(
      signInWithGoogleLoopback(
        clientId: 'test-client-id',
        launch: (_) async => false,
      ),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });

  test('throws when Google reports an error on the redirect', () async {
    await expectLater(
      signInWithGoogleLoopback(
        clientId: 'test-client-id',
        launch: _fakeLaunch({'error': 'access_denied'}),
      ),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });

  test('throws on a state mismatch instead of trusting an unmatched redirect', () async {
    await expectLater(
      signInWithGoogleLoopback(
        clientId: 'test-client-id',
        launch: (authUri) {
          final redirectUri = Uri.parse(authUri.queryParameters['redirect_uri']!);
          final callback = redirectUri.replace(
            queryParameters: {'state': 'wrong-state', 'code': 'auth-code-123'},
          );
          unawaited(() async {
            final client = HttpClient();
            try {
              final request = await client.getUrl(callback);
              await (await request.close()).drain<void>();
            } finally {
              client.close(force: true);
            }
          }());
          return Future<bool>.value(true);
        },
      ),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });

  test('throws when the token endpoint has no id_token in its response', () async {
    final tokenServer = await _FakeTokenServer.start(response: {'access_token': 'fake-access'});
    addTearDown(tokenServer.close);

    await expectLater(
      signInWithGoogleLoopback(
        clientId: 'test-client-id',
        tokenEndpoint: tokenServer.uri,
        launch: _fakeLaunch({'code': 'auth-code-123'}),
      ),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });

  test('throws when the token endpoint returns a non-200 response', () async {
    final tokenServer = await _FakeTokenServer.start(
      response: {'error': 'invalid_grant'},
      statusCode: 400,
    );
    addTearDown(tokenServer.close);

    await expectLater(
      signInWithGoogleLoopback(
        clientId: 'test-client-id',
        tokenEndpoint: tokenServer.uri,
        launch: _fakeLaunch({'code': 'auth-code-123'}),
      ),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });
}
