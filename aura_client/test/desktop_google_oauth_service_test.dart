import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:aura_client/features/auth/data/desktop_google_oauth_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

String _fakeIdToken(Map<String, Object?> payload) {
  String segment(Object value) =>
      base64Url.encode(utf8.encode(jsonEncode(value))).replaceAll('=', '');
  return '${segment({
        'alg': 'RS256'
      })}.${segment(payload)}.signature';
}

/// Stands in for the system browser completing the Google consent screen and
/// following its redirect back to the loopback server -- fired without
/// awaiting the response, the way a real external browser launch returns to
/// the caller long before the user finishes interacting with it. Awaiting it
/// here would deadlock: [DesktopGoogleOAuthService.signIn] only starts
/// listening for the callback *after* `openUrl` resolves.
Future<void> _simulateBrowserRedirect(
  String redirectUri, {
  required String state,
  String? code,
  String? error,
}) async {
  final client = HttpClient();
  try {
    final query = <String, String>{'state': state};
    if (code != null) query['code'] = code;
    if (error != null) query['error'] = error;
    final request = await client.getUrl(Uri.parse(redirectUri).replace(queryParameters: query));
    final response = await request.close();
    await response.drain<void>();
  } finally {
    client.close();
  }
}

void main() {
  test('rejects an empty client ID without opening the browser', () async {
    final service = DesktopGoogleOAuthService(clientId: '');
    var openedBrowser = false;

    await expectLater(
      () => service.signIn(openUrl: (_) async {
        openedBrowser = true;
        return true;
      }),
      throwsA(isA<StateError>()),
    );
    expect(openedBrowser, isFalse);
  });

  test('completes the loopback round trip and returns the parsed account', () async {
    final idToken = _fakeIdToken({
      'sub': 'user-123',
      'email': 'person@example.com',
      'name': 'Person Example',
      'picture': 'https://example.com/photo.png',
    });

    Map<String, String>? tokenRequestFields;
    final httpClient = MockClient((request) async {
      expect(request.url.toString(), 'https://oauth2.googleapis.com/token');
      tokenRequestFields = Uri.splitQueryString(request.body);
      return http.Response(jsonEncode({'id_token': idToken}), 200);
    });

    final service = DesktopGoogleOAuthService(
      clientId: 'desktop-client.apps.googleusercontent.com',
      httpClient: httpClient,
    );

    final account = await service.signIn(openUrl: (authorizeUri) async {
      expect(authorizeUri.queryParameters['code_challenge_method'], 'S256');
      unawaited(_simulateBrowserRedirect(
        authorizeUri.queryParameters['redirect_uri']!,
        state: authorizeUri.queryParameters['state']!,
        code: 'auth-code-xyz',
      ));
      return true;
    });

    expect(account.idToken, idToken);
    expect(account.sub, 'user-123');
    expect(account.email, 'person@example.com');
    expect(account.name, 'Person Example');
    expect(account.picture, 'https://example.com/photo.png');

    expect(tokenRequestFields, isNotNull);
    expect(tokenRequestFields!['code'], 'auth-code-xyz');
    expect(tokenRequestFields!['client_id'], 'desktop-client.apps.googleusercontent.com');
    expect(tokenRequestFields!['grant_type'], 'authorization_code');
    expect(tokenRequestFields!['code_verifier'], isNotEmpty);
    expect(tokenRequestFields!['redirect_uri'], startsWith('http://127.0.0.1:'));
  });

  test('rejects a callback whose state does not match', () async {
    final httpClient = MockClient((request) async {
      fail('token endpoint must not be called after a state mismatch');
    });
    final service = DesktopGoogleOAuthService(
      clientId: 'desktop-client.apps.googleusercontent.com',
      httpClient: httpClient,
    );

    await expectLater(
      () => service.signIn(openUrl: (authorizeUri) async {
        unawaited(_simulateBrowserRedirect(
          authorizeUri.queryParameters['redirect_uri']!,
          state: 'not-the-real-state',
          code: 'auth-code-xyz',
        ));
        return true;
      }),
      throwsA(isA<StateError>()),
    );
  });

  test('propagates a consent-denied error from the redirect', () async {
    final httpClient = MockClient((request) async {
      fail('token endpoint must not be called after a consent error');
    });
    final service = DesktopGoogleOAuthService(
      clientId: 'desktop-client.apps.googleusercontent.com',
      httpClient: httpClient,
    );

    await expectLater(
      () => service.signIn(openUrl: (authorizeUri) async {
        unawaited(_simulateBrowserRedirect(
          authorizeUri.queryParameters['redirect_uri']!,
          state: authorizeUri.queryParameters['state']!,
          error: 'access_denied',
        ));
        return true;
      }),
      throwsA(isA<StateError>()),
    );
  });
}
