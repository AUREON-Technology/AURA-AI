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
Future<bool> _simulateBrowserCallback(Uri authUri, Map<String, String> extraParams) async {
  final redirectUri = Uri.parse(authUri.queryParameters['redirect_uri']!);
  final state = authUri.queryParameters['state']!;
  final callback = redirectUri.replace(queryParameters: {'state': state, ...extraParams});
  final client = HttpClient();
  try {
    final request = await client.getUrl(callback);
    final response = await request.close();
    await response.drain<void>();
    return true;
  } finally {
    client.close(force: true);
  }
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
      launch: (authUri) => _simulateBrowserCallback(authUri, {'code': 'auth-code-123'}),
    );

    expect(result.idToken, 'fake-id-token');
    expect(tokenServer.lastRequestBody?['client_id'], 'test-client-id');
    expect(tokenServer.lastRequestBody?['code'], 'auth-code-123');
    expect(tokenServer.lastRequestBody?['grant_type'], 'authorization_code');
    expect(tokenServer.lastRequestBody?['code_verifier'], isNotEmpty);
  });

  test('throws immediately when no client id is configured', () async {
    await expectLater(
      signInWithGoogleLoopback(clientId: '', launch: (_) async => true),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });

  test('throws when the browser cannot be opened', () async {
    await expectLater(
      signInWithGoogleLoopback(clientId: 'test-client-id', launch: (_) async => false),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });

  test('throws when Google reports an error on the redirect', () async {
    await expectLater(
      signInWithGoogleLoopback(
        clientId: 'test-client-id',
        launch: (authUri) => _simulateBrowserCallback(authUri, {'error': 'access_denied'}),
      ),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });

  test('throws on a state mismatch instead of trusting an unmatched redirect', () async {
    await expectLater(
      signInWithGoogleLoopback(
        clientId: 'test-client-id',
        launch: (authUri) async {
          final redirectUri = Uri.parse(authUri.queryParameters['redirect_uri']!);
          final callback = redirectUri.replace(
            queryParameters: {'state': 'wrong-state', 'code': 'auth-code-123'},
          );
          final client = HttpClient();
          try {
            final request = await client.getUrl(callback);
            await (await request.close()).drain<void>();
          } finally {
            client.close(force: true);
          }
          return true;
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
        launch: (authUri) => _simulateBrowserCallback(authUri, {'code': 'auth-code-123'}),
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
        launch: (authUri) => _simulateBrowserCallback(authUri, {'code': 'auth-code-123'}),
      ),
      throwsA(isA<DesktopOAuthCancelledException>()),
    );
  });
}
