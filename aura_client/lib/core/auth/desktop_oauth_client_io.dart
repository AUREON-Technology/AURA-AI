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
/// 5. Exchange the authorization code for an ID token **through the AURA backend**
///    (`POST /api/v3/auth/desktop/exchange`, [_exchangeCodeViaBackend]). The app sends the
///    one-time `code`, its PKCE `code_verifier` and the loopback `redirect_uri`; the server adds
///    the OAuth client secret from its own environment, calls Google's token endpoint, and
///    returns only the ID token.
///
/// Why not call Google directly, as RFC 8252 §8.5 suggests for a public client? Because Google
/// does not treat this client as public. Confirmed live 2026-11-17: for the registered
/// **"Desktop app"**-type client (`core/config/config.dart::googleDesktopClientId`, shown as type
/// Desktop in Google Cloud Console), Google's token endpoint answers a PKCE-only exchange with
/// `invalid_request: client_secret is missing`. An earlier version of this comment read that as
/// "the client is registered as the wrong type" and told the next person not to ship a secret --
/// the client type was right and the diagnosis was wrong, and sign-in failed after the browser
/// step. Compiling the secret into the binary is what once leaked one (and forced a rotation),
/// so the secret stays on the server instead (`aura_backend/services/google_oauth.py`).
///
/// A build can still skip the backend: when [clientSecret] is non-empty (injected with
/// `--dart-define=GOOGLE_DESKTOP_CLIENT_SECRET=...`), or when no `backendExchangeEndpoint` is
/// given, the exchange goes straight to Google as before. Nothing needs that by default.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:aura_client/l10n/l10n.dart';
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
/// [backendExchangeEndpoint] is the AURA backend's `POST /api/v3/auth/desktop/exchange`. When
/// given (and [clientSecret] is empty) the code exchange runs there, so no secret is needed in
/// this binary; see the library comment for why Google requires one.
///
/// [clientSecret] is optional and empty by default. Pass one only to make this app exchange the
/// code with Google itself; see `core/config/config.dart::googleDesktopClientSecret`. A non-empty
/// secret takes precedence over [backendExchangeEndpoint].
///
/// [launch] and [tokenEndpoint] exist only so `desktop_oauth_client_io_test.dart` can exercise
/// this against a local fake server instead of a real browser and Google's real token endpoint
/// -- production callers never pass them.
Future<DesktopOAuthResult> signInWithGoogleLoopback({
  required String clientId,
  String clientSecret = '',
  Uri? backendExchangeEndpoint,
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
      throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInBrowserFailed);
    }

    final HttpRequest request;
    try {
      request = await server.first.timeout(timeout);
    } on TimeoutException {
      throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInTimedOut);
    }

    final params = request.uri.queryParameters;
    final code = params['code'];
    final returnedState = params['state'];
    final error = params['error'];

    if (error != null) {
      await _respond(
        request,
        success: false,
        message: currentL10n.coreDesktopSignInPageCancelled(error),
      );
      throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInGoogleError(error));
    }
    if (code == null || returnedState != state) {
      await _respond(
        request,
        success: false,
        message: currentL10n.coreDesktopSignInPageInvalidResponse,
      );
      throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInInvalidRedirect);
    }

    await _respond(request, success: true, message: currentL10n.coreDesktopSignInPageSuccess);

    final String idToken;
    if (backendExchangeEndpoint != null && clientSecret.isEmpty) {
      idToken = await _exchangeCodeViaBackend(
        endpoint: backendExchangeEndpoint,
        code: code,
        verifier: verifier,
        redirectUri: redirectUri,
      );
    } else {
      idToken = await _exchangeCodeForIdToken(
        tokenEndpoint: tokenEndpoint ?? Uri.parse(_tokenEndpoint),
        clientId: clientId,
        clientSecret: clientSecret,
        code: code,
        verifier: verifier,
        redirectUri: redirectUri,
      );
    }
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
      // Google demands the client secret for this client even though it is a "Desktop app" one
      // (confirmed live 2026-11-17; the client type is not the problem). The normal build never
      // reaches this path -- it exchanges through the backend, which holds the secret. Landing
      // here means a build with no `backendExchangeEndpoint` talking to Google directly.
      if (clientSecret.isEmpty && raw.contains('client_secret is missing')) {
        throw DesktopOAuthCancelledException(
          'Google requires a client secret to exchange the sign-in code for client id '
          '$clientId, and this build is calling Google directly without one. Sign-in normally '
          'runs through the AURA backend (POST /api/v3/auth/desktop/exchange), which holds the '
          'secret; use a build that does, or inject GOOGLE_DESKTOP_CLIENT_SECRET at build '
          'time. Raw response: $raw',
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

/// Trades the one-time [code] for Google's ID token via the AURA backend, which adds the OAuth
/// client secret this binary does not carry. Sends no credential of the user's -- the code is
/// useless without [verifier], which never leaves this process except to this call.
///
/// Every failure becomes a [DesktopOAuthCancelledException] whose message is what the server said
/// (its `detail`, already written to be shown) or a plain sentence when nothing answered, so the
/// sign-in screen never has to guess between "Google said no" and "AURA is unreachable".
Future<String> _exchangeCodeViaBackend({
  required Uri endpoint,
  required String code,
  required String verifier,
  required String redirectUri,
}) async {
  final client = HttpClient();
  try {
    final String raw;
    final int status;
    try {
      final request = await client.postUrl(endpoint).timeout(const Duration(seconds: 30));
      request.headers.contentType = ContentType.json;
      request.write(jsonEncode({
        'code': code,
        'code_verifier': verifier,
        'redirect_uri': redirectUri,
      }));
      final response = await request.close().timeout(const Duration(seconds: 30));
      status = response.statusCode;
      raw = await response.transform(utf8.decoder).join().timeout(const Duration(seconds: 30));
    } on SocketException {
      throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInServerUnreachable);
    } on HandshakeException {
      throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInServerInsecure);
    } on HttpException {
      throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInServerBadAnswer);
    } on TimeoutException {
      throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInServerTimedOut);
    }

    Object? decoded;
    try {
      decoded = jsonDecode(raw);
    } on FormatException {
      decoded = null;
    }
    final body = decoded is Map<String, dynamic> ? decoded : const <String, dynamic>{};

    if (status != 200) {
      final detail = body['detail'];
      throw DesktopOAuthCancelledException(
        detail is String && detail.isNotEmpty
            ? detail
            : currentL10n.coreDesktopSignInServerFailed(status),
      );
    }
    final idToken = body['id_token'];
    if (idToken is! String || idToken.isEmpty) {
      throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInNoIdentityToken);
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
<h2>${success ? currentL10n.coreDesktopSignInPageSignedIn : currentL10n.coreDesktopSignInPageFailed}</h2>
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
