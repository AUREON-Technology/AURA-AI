import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

/// Fields pulled out of a Google ID token's JWT payload for local UI display
/// only. The backend independently re-verifies [idToken]'s signature,
/// issuer, expiry, and audience (`services/security.py::verify_identity_token`)
/// before trusting any of it -- nothing here is a security boundary.
class DesktopGoogleAccount {
  const DesktopGoogleAccount({
    required this.idToken,
    required this.sub,
    required this.email,
    this.name,
    this.picture,
  });

  final String idToken;
  final String sub;
  final String email;
  final String? name;
  final String? picture;
}

/// Google's "installed app" OAuth 2.0 flow (RFC 8252 loopback redirect +
/// PKCE) for the desktop platforms `google_sign_in` has no plugin
/// implementation for (Linux, Windows; see AURA_UPGRADE_ROADMAP.md phase J5).
///
/// No client secret is embedded: [clientId] must be a Google Cloud Console
/// OAuth client of type "Desktop app", which Google treats as a public
/// client and issues without one. A confidential ("Web application") client
/// secret must never ship inside a distributed desktop binary.
class DesktopGoogleOAuthService {
  DesktopGoogleOAuthService({required this.clientId, http.Client? httpClient})
      : _http = httpClient ?? http.Client();

  final String clientId;
  final http.Client _http;

  static const _authorizeEndpoint = 'https://accounts.google.com/o/oauth2/v2/auth';
  static const _tokenEndpoint = 'https://oauth2.googleapis.com/token';
  static const _scopes = 'openid email profile';
  static const _callbackTimeout = Duration(minutes: 5);

  /// Opens the system browser to Google's consent screen, listens on a
  /// loopback port for the redirect, and exchanges the returned code for an
  /// ID token. [openUrl] is injectable for tests; defaults to
  /// `url_launcher`'s external-application launch (the same mechanism
  /// `github_connection_page.dart` uses for the GitHub OAuth connect flow).
  Future<DesktopGoogleAccount> signIn({
    Future<bool> Function(Uri authorizeUri)? openUrl,
  }) async {
    if (clientId.isEmpty) {
      throw StateError(
        'Desktop Google sign-in is not configured: no GOOGLE_DESKTOP_CLIENT_ID '
        'was supplied at build time.',
      );
    }

    final verifier = _randomUrlSafeToken(64);
    final challenge = base64UrlEncode(sha256.convert(utf8.encode(verifier)).bytes)
        .replaceAll('=', '');
    final state = _randomUrlSafeToken(16);

    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    try {
      final redirectUri = 'http://127.0.0.1:${server.port}/';
      final authorizeUri = Uri.parse(_authorizeEndpoint).replace(queryParameters: {
        'client_id': clientId,
        'redirect_uri': redirectUri,
        'response_type': 'code',
        'scope': _scopes,
        'state': state,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
        'access_type': 'online',
        'prompt': 'select_account',
      });

      final launch = openUrl ??
          (uri) => launchUrl(uri, mode: LaunchMode.externalApplication);
      final opened = await launch(authorizeUri);
      if (!opened) {
        throw StateError('Could not open the system browser for Google sign-in.');
      }

      final code = await _awaitCallback(server, expectedState: state);
      return _exchangeCode(code, verifier: verifier, redirectUri: redirectUri);
    } finally {
      await server.close(force: true);
    }
  }

  Future<String> _awaitCallback(HttpServer server, {required String expectedState}) async {
    final completer = Completer<String>();
    final sub = server.listen((request) async {
      final params = request.uri.queryParameters;
      final error = params['error'];
      final code = params['code'];
      final returnedState = params['state'];
      final ok = error == null && code != null && returnedState == expectedState;

      request.response
        ..statusCode = ok ? HttpStatus.ok : HttpStatus.badRequest
        ..headers.contentType = ContentType.html
        ..write(_callbackPage(success: ok));
      await request.response.close();

      if (completer.isCompleted) return;
      if (ok) {
        completer.complete(code);
      } else {
        completer.completeError(StateError(error ?? 'Invalid sign-in callback.'));
      }
    }, onError: (Object e) {
      if (!completer.isCompleted) completer.completeError(e);
    });

    try {
      return await completer.future.timeout(_callbackTimeout);
    } finally {
      await sub.cancel();
    }
  }

  Future<DesktopGoogleAccount> _exchangeCode(
    String code, {
    required String verifier,
    required String redirectUri,
  }) async {
    final response = await _http
        .post(
          Uri.parse(_tokenEndpoint),
          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          body: {
            'client_id': clientId,
            'code': code,
            'code_verifier': verifier,
            'grant_type': 'authorization_code',
            'redirect_uri': redirectUri,
          },
        )
        .timeout(const Duration(seconds: 30));

    if (response.statusCode != 200) {
      throw StateError(
        'Google token exchange failed: ${response.statusCode} ${response.body}',
      );
    }

    final body = jsonDecode(response.body) as Map<String, dynamic>;
    final idToken = body['id_token'] as String?;
    if (idToken == null || idToken.isEmpty) {
      throw StateError('Google token response did not include an id_token.');
    }
    return _parseIdToken(idToken);
  }

  static DesktopGoogleAccount _parseIdToken(String idToken) {
    final parts = idToken.split('.');
    if (parts.length != 3) {
      throw StateError('Malformed Google ID token.');
    }
    final payload = jsonDecode(
      utf8.decode(base64Url.decode(base64Url.normalize(parts[1]))),
    ) as Map<String, dynamic>;
    return DesktopGoogleAccount(
      idToken: idToken,
      sub: payload['sub'] as String? ?? '',
      email: payload['email'] as String? ?? '',
      name: payload['name'] as String?,
      picture: payload['picture'] as String?,
    );
  }

  static String _randomUrlSafeToken(int length) {
    final random = Random.secure();
    final bytes = List<int>.generate(length, (_) => random.nextInt(256));
    return base64UrlEncode(bytes).replaceAll('=', '');
  }

  static String _callbackPage({required bool success}) {
    final message = success
        ? 'Signed in. You can close this window and return to AURA.'
        : 'Sign-in failed. You can close this window and try again in AURA.';
    return '<!DOCTYPE html><html><head><title>AURA</title></head>'
        '<body style="font-family: sans-serif; text-align: center; padding-top: 3em;">'
        '<p>$message</p></body></html>';
  }
}
