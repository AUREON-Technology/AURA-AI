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

  // Regression guard for the incident this flow was rebuilt around: the fix for "client_secret
  // is missing" is registering a Desktop-app client, NOT shipping a secret. If the error ever
  // says only "HTTP 400", the next person re-adds a secret; so it has to name the real cause.
  test('diagnoses a wrong-typed OAuth client when Google demands a secret', () async {
    final tokenServer = await _FakeTokenServer.start(
      response: {'error': 'invalid_request', 'error_description': 'client_secret is missing.'},
      statusCode: 400,
    );
    addTearDown(tokenServer.close);

    await expectLater(
      signInWithGoogleLoopback(
        clientId: 'wrongly-typed-client-id',
        tokenEndpoint: tokenServer.uri,
        launch: _fakeLaunch({'code': 'auth-code-123'}),
      ),
      throwsA(
        isA<DesktopOAuthCancelledException>().having(
          (e) => e.message,
          'message',
          allOf(contains('Desktop app'), contains('wrongly-typed-client-id')),
        ),
      ),
    );
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
