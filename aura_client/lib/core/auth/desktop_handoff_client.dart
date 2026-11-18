/// The web half of desktop sign-in through the browser (module readiness WP12): what the
/// `/desktop-sign-in` page (`features/auth/presentation/desktop_sign_in_page.dart`) checks and
/// sends. The desktop half is `desktop_oauth_client_io.dart`'s `signInWithBrowserHandoff`; the
/// server half is `aura_backend/services/desktop_handoff.py`.
///
/// Web-safe on purpose (no `dart:io`): this runs in the browser.
library;

import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

import '../../l10n/l10n.dart';
import '../config/backend_url_provider.dart';

/// Only this computer's desktop app, on a port it bound: the page never sends a code anywhere
/// else, whatever the link says. The server applies the same rule (`is_loopback_redirect`).
final _loopbackRedirect = RegExp(r'^http://127\.0\.0\.1:(\d{1,5})/$');

/// RFC 7636 S256: base64url SHA-256, unpadded -- the only form the server accepts.
final _pkceChallenge = RegExp(r'^[A-Za-z0-9_-]{43}$');

/// The desktop app's `state` is base64url; anything else did not come from it.
final _state = RegExp(r'^[A-Za-z0-9_-]{16,128}$');

/// What the desktop app asked for in the page's query string, once it has been checked.
@immutable
class DesktopHandoffRequest {
  const DesktopHandoffRequest._({
    required this.redirectUri,
    required this.state,
    required this.codeChallenge,
  });

  final String redirectUri;
  final String state;
  final String codeChallenge;

  /// The request in [query], or null when any part of it is missing or malformed -- above all a
  /// `redirect_uri` that is not `http://127.0.0.1:<port>/`. The page refuses a null request
  /// before anyone signs in.
  static DesktopHandoffRequest? fromQuery(Map<String, String> query) {
    final redirectUri = query['redirect_uri'] ?? '';
    final state = query['state'] ?? '';
    final challenge = query['code_challenge'] ?? '';
    final match = _loopbackRedirect.firstMatch(redirectUri);
    if (match == null) return null;
    final port = int.parse(match.group(1)!);
    if (port < 1 || port > 65535) return null;
    if (!_state.hasMatch(state) || !_pkceChallenge.hasMatch(challenge)) return null;
    return DesktopHandoffRequest._(redirectUri: redirectUri, state: state, codeChallenge: challenge);
  }

  /// Where the browser goes with the one-time [code]: the desktop app's listener, carrying back
  /// the `state` it made so it can match the answer to its own request.
  Uri returnUri(String code) =>
      Uri.parse(redirectUri).replace(queryParameters: {'code': code, 'state': state});
}

/// The hand-off did not produce a code. [message] is written to be shown: the server's `detail`
/// when it gave one, otherwise a sentence of the app's own.
class DesktopHandoffFailure implements Exception {
  const DesktopHandoffFailure(this.message, {this.statusCode});

  final String message;
  final int? statusCode;

  @override
  String toString() => 'DesktopHandoffFailure($statusCode): $message';
}

/// `POST /api/v3/auth/desktop/handoff`: trades the Firebase ID token the page just obtained for a
/// one-time code bound to the desktop app's PKCE challenge and loopback redirect. Mints no
/// session: the desktop app's exchange does that, with the verifier only it holds.
class DesktopHandoffClient {
  DesktopHandoffClient(this._ref, {http.Client? httpClient}) : _http = httpClient;

  final Ref _ref;
  final http.Client? _http;

  Future<String> requestCode({
    required String idToken,
    required DesktopHandoffRequest request,
  }) async {
    final uri = Uri.parse('${_ref.read(backendUrlProvider)}/api/v3/auth/desktop/handoff');
    final headers = {
      'Authorization': 'Bearer $idToken',
      'Content-Type': 'application/json',
    };
    final body = jsonEncode({
      'code_challenge': request.codeChallenge,
      'redirect_uri': request.redirectUri,
    });
    final http.Response response;
    try {
      response = await (_http?.post(uri, headers: headers, body: body) ??
              http.post(uri, headers: headers, body: body))
          .timeout(const Duration(seconds: 30));
    } on TimeoutException {
      throw DesktopHandoffFailure(currentL10n.desktopSignInServerUnreachable);
    } on http.ClientException {
      throw DesktopHandoffFailure(currentL10n.desktopSignInServerUnreachable);
    }

    Object? decoded;
    try {
      decoded = jsonDecode(response.body);
    } on FormatException {
      decoded = null;
    }
    final answer = decoded is Map<String, dynamic> ? decoded : const <String, dynamic>{};
    if (response.statusCode != 200) {
      final detail = answer['detail'];
      throw DesktopHandoffFailure(
        detail is String && detail.isNotEmpty
            ? detail
            : currentL10n.desktopSignInServerFailed(response.statusCode),
        statusCode: response.statusCode,
      );
    }
    final code = answer['code'];
    if (code is! String || code.isEmpty) {
      throw DesktopHandoffFailure(currentL10n.desktopSignInServerFailed(response.statusCode));
    }
    return code;
  }
}

final desktopHandoffClientProvider =
    Provider<DesktopHandoffClient>((ref) => DesktopHandoffClient(ref));

/// Sends this browser tab to the desktop app's listener. The same tab, not a new one: the page has
/// nothing left to do, and a popup blocker would stop a new window opened after an `await`.
final desktopHandoffReturnProvider = Provider<Future<bool> Function(Uri uri)>(
  (ref) => (uri) => launchUrl(uri, webOnlyWindowName: '_self'),
);
