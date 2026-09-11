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
      clientSecret: 'test-client-secret',
      tokenEndpoint: tokenServer.uri,
      launch: _fakeLaunch({'code': 'auth-code-123'}),
    );

    expect(result.idToken, 'fake-id-token');
    expect(tokenServer.lastRequestBody?['client_id'], 'test-client-id');
    expect(tokenServer.lastRequestBody?['code'], 'auth-code-123');
    expect(tokenServer.lastRequestBody?['grant_type'], 'authorization_code');
    expect(tokenServer.lastRequestBody?['code_verifier'], isNotEmpty);
  });

  // Google's token endpoint rejects this app's registered client without a client_secret even
  // though PKCE is present (confirmed live -- see signInWithGoogleLoopback's doc comment), so
  // the exchange must always include one.
  test('sends the client secret in the token exchange body', () async {
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

  test('throws immediately when no client id is configured', () async {
    await expectLater(
      signInWithGoogleLoopback(
        clientId: '',
        clientSecret: 'test-client-secret',
        launch: (_) async => true,
      ),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });

  test('throws immediately when no client secret is configured', () async {
    await expectLater(
      signInWithGoogleLoopback(
        clientId: 'test-client-id',
        clientSecret: '',
        launch: (_) async => true,
      ),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });

  test('throws when the browser cannot be opened', () async {
    await expectLater(
      signInWithGoogleLoopback(
        clientId: 'test-client-id',
        clientSecret: 'test-client-secret',
        launch: (_) async => false,
      ),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });

  test('throws when Google reports an error on the redirect', () async {
    await expectLater(
      signInWithGoogleLoopback(
        clientId: 'test-client-id',
        clientSecret: 'test-client-secret',
        launch: _fakeLaunch({'error': 'access_denied'}),
      ),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });

  test('throws on a state mismatch instead of trusting an unmatched redirect', () async {
    await expectLater(
      signInWithGoogleLoopback(
        clientId: 'test-client-id',
        clientSecret: 'test-client-secret',
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
        clientSecret: 'test-client-secret',
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
        clientSecret: 'test-client-secret',
        tokenEndpoint: tokenServer.uri,
        launch: _fakeLaunch({'code': 'auth-code-123'}),
      ),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });
}
