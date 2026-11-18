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
///
/// [signInWithBrowserHandoff] is the second loopback flow in this file: the browser signs in with
/// Firebase (Google, Apple or email) on the web app's `/desktop-sign-in` page and hands a one-time
/// code back here (module readiness WP12). Both flows share the listener, PKCE and redirect check.
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
  DesktopOAuthCancelledException(this.message, {this.statusCode, this.refusal});

  final String message;

  /// The server's `X-Aura-Refusal` marker on a refusal, e.g. `plan_required` when the fix is a Pro
  /// or Team plan (module readiness WP11). Null when absent.
  final String? refusal;

  /// The AURA server's HTTP status when it answered the exchange with a refusal; null for every
  /// failure that was not such an answer (browser, timeout, network). `AuthNotifier` reads it to
  /// tell "the server said no to this account" (403) apart from "try again".
  final int? statusCode;

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

/// What [signInWithBrowserHandoff] got back: the AURA server's desktop session, exactly as
/// `POST /api/v3/auth/desktop/firebase-exchange` answered it (the same shape as
/// `/desktop/login`: `access_token`, `refresh_token`, `token_type`, `expires_in`, `device_id`).
/// `TokenManager.applyDesktopSession` takes it from here.
class DesktopHandoffResult {
  DesktopHandoffResult({required this.tokenResponse});

  final Map<String, dynamic> tokenResponse;

  String get accessToken => tokenResponse['access_token'] as String;
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
    final pkce = _newPkce();
    final verifier = pkce.verifier;
    final state = _randomUrlSafeToken(24);

    final authUri = Uri.parse(_authorizationEndpoint).replace(queryParameters: {
      'client_id': clientId,
      'redirect_uri': redirectUri,
      'response_type': 'code',
      'scope': 'openid email profile',
      'code_challenge': pkce.challenge,
      'code_challenge_method': 'S256',
      'state': state,
    });

    final opened = await doLaunch(authUri);
    if (!opened) {
      throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInBrowserFailed);
    }

    final code = await _awaitRedirectCode(
      server,
      state: state,
      timeout: timeout,
      reportedError: currentL10n.coreDesktopSignInGoogleError,
    );

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

/// Desktop sign-in with Firebase (Google, Apple or email) through the browser -- module
/// readiness WP12. FlutterFire has no Linux implementation and no provider sign-in on Windows, so
/// this app cannot sign in with Firebase itself; the web app does it and hands the result back,
/// the same RFC 8252 loopback shape as [signInWithGoogleLoopback]:
///
/// 1. Bind a listener on `127.0.0.1` and make a PKCE verifier/challenge and a random `state`.
/// 2. Open [signInPage] (the web app's `/desktop-sign-in`) with `redirect_uri`, `state` and
///    `code_challenge`. The page signs the person in with Firebase, trades the Firebase ID token
///    at `POST /api/v3/auth/desktop/handoff` for a one-time code bound to the challenge and the
///    redirect, and sends the browser to `redirect_uri?code=&state=`.
/// 3. Check `state`, then redeem the code with the verifier at [exchangeEndpoint]
///    (`POST /api/v3/auth/desktop/firebase-exchange`), which applies the desktop gate and issues
///    the device-bound session.
///
/// The Firebase ID token never reaches this process and never travels in a URL; the code alone is
/// useless without the verifier, which never leaves this process except to [exchangeEndpoint].
///
/// Throws [DesktopOAuthCancelledException] for every failure. When the server refused the
/// exchange its `detail` is the message and [DesktopOAuthCancelledException.statusCode] is set.
///
/// [launch] exists only so `desktop_oauth_client_io_test.dart` can play the browser -- production
/// callers never pass it.
Future<DesktopHandoffResult> signInWithBrowserHandoff({
  required Uri signInPage,
  required Uri exchangeEndpoint,
  String? deviceName,
  Duration timeout = const Duration(minutes: 5),
  @visibleForTesting Future<bool> Function(Uri uri)? launch,
}) async {
  final doLaunch = launch ?? _defaultLaunch;

  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  try {
    final redirectUri = 'http://127.0.0.1:${server.port}/';
    final pkce = _newPkce();
    final state = _randomUrlSafeToken(24);

    final pageUri = signInPage.replace(queryParameters: {
      ...signInPage.queryParameters,
      'redirect_uri': redirectUri,
      'state': state,
      'code_challenge': pkce.challenge,
    });

    final opened = await doLaunch(pageUri);
    if (!opened) {
      throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInBrowserFailed);
    }

    final code = await _awaitRedirectCode(
      server,
      state: state,
      timeout: timeout,
      reportedError: currentL10n.coreDesktopSignInBrowserError,
    );

    // The server bounds the name at 128 characters (`DesktopFirebaseExchangeRequest`); a longer
    // hostname must not turn a good sign-in into a 422.
    final name = (deviceName == null || deviceName.isEmpty)
        ? null
        : (deviceName.length > 128 ? deviceName.substring(0, 128) : deviceName);
    final answer = await _postJsonToBackend(exchangeEndpoint, {
      'code': code,
      'code_verifier': pkce.verifier,
      'redirect_uri': redirectUri,
      'device_name': ?name,
    });
    if (answer.status != 200) throw _backendRefusal(answer);

    final body = answer.body;
    if (body['access_token'] is! String ||
        (body['access_token'] as String).isEmpty ||
        body['refresh_token'] is! String ||
        body['expires_in'] is! int) {
      throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInNoSession);
    }
    return DesktopHandoffResult(tokenResponse: body);
  } finally {
    await server.close(force: true);
  }
}

/// RFC 7636 S256: a 64-byte random verifier (86 base64url characters, inside the 43-128 the
/// server accepts) and its unpadded SHA-256 challenge.
({String verifier, String challenge}) _newPkce() {
  final verifier = _randomUrlSafeToken(64);
  final challenge =
      base64UrlEncode(sha256.convert(utf8.encode(verifier)).bytes).replaceAll('=', '');
  return (verifier: verifier, challenge: challenge);
}

/// Waits for the one request the browser makes to the loopback listener, answers it with a small
/// page so the tab does not hang, and returns its `code`. Anything but a `code` with the exact
/// [state] this process made is refused: a redirect it cannot match to its own request is never
/// trusted (CSRF). An `error` parameter becomes [reportedError]'s message.
Future<String> _awaitRedirectCode(
  HttpServer server, {
  required String state,
  required Duration timeout,
  required String Function(String error) reportedError,
}) async {
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
    throw DesktopOAuthCancelledException(reportedError(error));
  }
  if (code == null || code.isEmpty || returnedState != state) {
    await _respond(
      request,
      success: false,
      message: currentL10n.coreDesktopSignInPageInvalidResponse,
    );
    throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInInvalidRedirect);
  }

  await _respond(request, success: true, message: currentL10n.coreDesktopSignInPageSuccess);
  return code;
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
  final answer = await _postJsonToBackend(endpoint, {
    'code': code,
    'code_verifier': verifier,
    'redirect_uri': redirectUri,
  });
  if (answer.status != 200) throw _backendRefusal(answer);
  final idToken = answer.body['id_token'];
  if (idToken is! String || idToken.isEmpty) {
    throw DesktopOAuthCancelledException(currentL10n.coreDesktopSignInNoIdentityToken);
  }
  return idToken;
}

/// One JSON `POST` to the AURA server, for both desktop exchanges. Sends no credential of the
/// user's. Every way of getting no answer becomes a [DesktopOAuthCancelledException] with a plain
/// sentence, so the sign-in screen never has to guess between "the server said no" and "AURA is
/// unreachable"; an answer comes back as its status and its JSON object (empty when the body was
/// not one).
Future<({int status, Map<String, dynamic> body, String? refusal})> _postJsonToBackend(
  Uri endpoint,
  Map<String, Object?> payload,
) async {
  final client = HttpClient();
  try {
    final String raw;
    final int status;
    final String? refusal;
    try {
      final request = await client.postUrl(endpoint).timeout(const Duration(seconds: 30));
      request.headers.contentType = ContentType.json;
      request.write(jsonEncode(payload));
      final response = await request.close().timeout(const Duration(seconds: 30));
      status = response.statusCode;
      refusal = response.headers.value('x-aura-refusal');
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
    return (
      status: status,
      body: decoded is Map<String, dynamic> ? decoded : <String, dynamic>{},
      refusal: refusal,
    );
  } finally {
    client.close(force: true);
  }
}

/// A non-200 answer from the AURA server, worded by its own `detail` (already written to be shown)
/// or, without one, by the status.
DesktopOAuthCancelledException _backendRefusal(
  ({int status, Map<String, dynamic> body, String? refusal}) answer,
) {
  final detail = answer.body['detail'];
  return DesktopOAuthCancelledException(
    detail is String && detail.isNotEmpty
        ? detail
        : currentL10n.coreDesktopSignInServerFailed(answer.status),
    statusCode: answer.status,
    refusal: answer.refusal,
  );
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
