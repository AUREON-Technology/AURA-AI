/// `dart:io`-backed half of `desktop_oauth_client.dart` -- see that file for why this exists
/// and how it's selected.
///
/// The flow (RFC 8252, "OAuth 2.0 for Native Apps", loopback IP redirect variant):
///
/// 1. Bind an ephemeral local HTTP server on `127.0.0.1`.
/// 2. Build Google's authorization URL with PKCE (`code_challenge`/`S256`) and a random `state`,
///    redirecting to that loopback address.
/// 3. Open it in the system browser via `url_launcher` (the same
///    `launchUrl(..., mode: LaunchMode.externalApplication)` pattern
///    `features/engineering/presentation/github_connection_page.dart` already uses for GitHub's
///    OAuth authorize page).
/// 4. Wait for exactly one request to land on the local server -- that's Google's redirect
///    carrying `?code=&state=`. Answer it with a small HTML page so the browser tab shows
///    something instead of hanging, then stop listening.
/// 5. Exchange the authorization code for tokens directly with Google's token endpoint, proving
///    possession of the `code` with the PKCE `code_verifier` and nothing else. RFC 8252 §8.5 --
///    and Google's own installed-app guidance -- treat a native app as a *public* client: PKCE
///    is the proof, and `client_secret` is left out of the request entirely.
///
/// Needs a real **"Desktop app"**-type OAuth client registered in Google Cloud Console
/// (`core/config/config.dart::googleDesktopClientId`) -- see
/// `aura_backend/config.py::google_desktop_client_id`'s doc comment for why it can't be the same
/// client id as the existing mobile/web login path. That type is not cosmetic: a "Web
/// application"-type client is *confidential*, so its token endpoint refuses the exchange above
/// with `invalid_request: client_secret is missing` however correct the PKCE is. That error was
/// once read as a quirk of desktop login and worked around by compiling a `GOCSPX-...` secret
/// into the client; it actually means "this client is registered as the wrong type", and
/// [_exchangeCodeForIdToken] now says so when Google returns it.
///
/// A build whose configured client genuinely is a confidential one can still supply a secret via
/// `--dart-define=GOOGLE_DESKTOP_CLIENT_SECRET=...`, but nothing needs it by default.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:url_launcher/url_launcher.dart';

class DesktopOAuthCancelledException implements Exception {
  DesktopOAuthCancelledException(this.message);

  final String message;

  @override
  String toString() => 'DesktopOAuthCancelledException: $message';
}

class DesktopOAuthResult {
  DesktopOAuthResult({required this.idToken});

  /// Google's raw OAuth2 ID token (a signed JWT) -- handed to the backend's
  /// `POST /api/v3/auth/desktop/login`, which verifies it directly
  /// (`services/security.py::verify_identity_token`). Not a Firebase ID token.
  final String idToken;
}

const _authorizationEndpoint = 'https://accounts.google.com/o/oauth2/v2/auth';
const _tokenEndpoint = 'https://oauth2.googleapis.com/token';

/// Runs the full loopback flow and returns a real Google ID token, or throws
/// [DesktopOAuthCancelledException] if the user cancels, the browser can't be opened, Google
/// reports an error, or nothing arrives within [timeout].
///
/// [clientSecret] is optional and empty by default -- a "Desktop app" client is public and
/// authenticates with PKCE alone, so an empty one is the normal case and is omitted from the
/// token exchange rather than refused. Pass one only for a confidential client; see
/// `core/config/config.dart::googleDesktopClientSecret`.
///
/// [launch] and [tokenEndpoint] exist only so `desktop_oauth_client_io_test.dart` can exercise
/// this against a local fake server instead of a real browser and Google's real token endpoint
/// -- production callers never pass them.
Future<DesktopOAuthResult> signInWithGoogleLoopback({
  required String clientId,
  String clientSecret = '',
  Duration timeout = const Duration(minutes: 5),
  @visibleForTesting Future<bool> Function(Uri uri)? launch,
  @visibleForTesting Uri? tokenEndpoint,
}) async {
  if (clientId.isEmpty) {
    throw DesktopOAuthCancelledException(
      'No GOOGLE_DESKTOP_CLIENT_ID configured for this build -- desktop sign-in is not set up '
      'yet. See aura_backend/config.py::google_desktop_client_id.',
    );
  }
  final doLaunch = launch ?? _defaultLaunch;

  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  try {
    final redirectUri = 'http://127.0.0.1:${server.port}/';
    final verifier = _randomUrlSafeToken(64);
    final challenge = base64UrlEncode(sha256.convert(utf8.encode(verifier)).bytes)
        .replaceAll('=', '');
    final state = _randomUrlSafeToken(24);

    final authUri = Uri.parse(_authorizationEndpoint).replace(queryParameters: {
      'client_id': clientId,
      'redirect_uri': redirectUri,
      'response_type': 'code',
      'scope': 'openid email profile',
      'code_challenge': challenge,
      'code_challenge_method': 'S256',
      'state': state,
    });

    final opened = await doLaunch(authUri);
    if (!opened) {
      throw DesktopOAuthCancelledException('Could not open the system browser.');
    }

    final HttpRequest request;
    try {
      request = await server.first.timeout(timeout);
    } on TimeoutException {
      throw DesktopOAuthCancelledException(
        'Timed out waiting for the browser sign-in to complete.',
      );
    }

    final params = request.uri.queryParameters;
    final code = params['code'];
    final returnedState = params['state'];
    final error = params['error'];

    if (error != null) {
      await _respond(request, success: false, message: 'Sign-in was cancelled ($error).');
      throw DesktopOAuthCancelledException('Google returned an error: $error');
    }
    if (code == null || returnedState != state) {
      await _respond(request, success: false, message: 'Sign-in failed: invalid response.');
      throw DesktopOAuthCancelledException(
        'Missing authorization code or a state mismatch (possible CSRF).',
      );
    }

    await _respond(request, success: true, message: 'Signed in -- you can close this tab.');

    final idToken = await _exchangeCodeForIdToken(
      tokenEndpoint: tokenEndpoint ?? Uri.parse(_tokenEndpoint),
      clientId: clientId,
      clientSecret: clientSecret,
      code: code,
      verifier: verifier,
      redirectUri: redirectUri,
    );
    return DesktopOAuthResult(idToken: idToken);
  } finally {
    await server.close(force: true);
  }
}

Future<bool> _defaultLaunch(Uri uri) => launchUrl(uri, mode: LaunchMode.externalApplication);

Future<void> _respond(HttpRequest request, {required bool success, required String message}) async {
  request.response
    ..statusCode = 200
    ..headers.contentType = ContentType.html
    ..write(_resultPage(success: success, message: message));
  await request.response.close();
}

Future<String> _exchangeCodeForIdToken({
  required Uri tokenEndpoint,
  required String clientId,
  required String clientSecret,
  required String code,
  required String verifier,
  required String redirectUri,
}) async {
  final client = HttpClient();
  try {
    final request = await client.postUrl(tokenEndpoint);
    request.headers.contentType = ContentType('application', 'x-www-form-urlencoded');
    request.write(Uri(queryParameters: {
      'client_id': clientId,
      'code': code,
      'code_verifier': verifier,
      'grant_type': 'authorization_code',
      'redirect_uri': redirectUri,
      // Omitted entirely -- not sent empty -- for a public "Desktop app" client, whose only
      // proof is the `code_verifier` above. Google rejects `client_secret=` (present but blank)
      // the same way it rejects a wrong one, so "absent" and "empty" are not interchangeable
      // here. Only a build that injected one for a confidential client sends it.
      if (clientSecret.isNotEmpty) 'client_secret': clientSecret,
    }).query);

    final response = await request.close();
    final raw = await response.transform(utf8.decoder).join();
    if (response.statusCode != 200) {
      // Google answers a secret-less exchange against a *confidential* client this way. It reads
      // like "you forgot a credential", but for this flow it means the configured client id is
      // registered as the wrong type -- a public "Desktop app" client never asks for a secret.
      // Saying so here is what keeps the next person from re-fixing it by shipping a secret.
      if (clientSecret.isEmpty && raw.contains('client_secret is missing')) {
        throw DesktopOAuthCancelledException(
          'Google rejected the PKCE-only token exchange because client id $clientId is not '
          'registered as a "Desktop app" OAuth client -- only a confidential ("Web '
          'application") client demands a client_secret. Register a Desktop app client in '
          'Google Cloud Console and set GOOGLE_DESKTOP_CLIENT_ID (and the backend\'s matching '
          'google_desktop_client_id) to it. Raw response: $raw',
        );
      }
      throw DesktopOAuthCancelledException(
        'Google token exchange failed: HTTP ${response.statusCode}: $raw',
      );
    }
    final decoded = jsonDecode(raw) as Map<String, dynamic>;
    final idToken = decoded['id_token'] as String?;
    if (idToken == null) {
      throw DesktopOAuthCancelledException('Google token response had no id_token.');
    }
    return idToken;
  } finally {
    client.close(force: true);
  }
}

String _randomUrlSafeToken(int length) {
  final random = Random.secure();
  final bytes = List<int>.generate(length, (_) => random.nextInt(256));
  return base64UrlEncode(bytes).replaceAll('=', '');
}

String _resultPage({required bool success, required String message}) => '''
<!doctype html>
<html>
<head><meta charset="utf-8"><title>AURA</title></head>
<body style="font-family: sans-serif; text-align: center; margin-top: 3rem;">
<h2>${success ? 'Signed in' : 'Sign-in failed'}</h2>
<p>$message</p>
</body>
</html>
''';

/// This machine's hostname, sent with the desktop sign-in so the account owner can tell this
/// device apart in Settings > Desktop devices (FSA-004); `null` when the platform will not say.
String? desktopDeviceName() {
  try {
    final name = Platform.localHostname.trim();
    return name.isEmpty ? null : name;
  } catch (_) {
    return null;
  }
}
