import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

/// Loopback OAuth for Linux/Windows where `google_sign_in` has no plugin (Unit 7).
///
/// Opens the system browser to Google's authorization endpoint, listens on
/// `http://127.0.0.1:<ephemeral>/callback`, and exchanges the code + PKCE verifier
/// for an ID token. macOS/mobile keep using `google_sign_in`.
class DesktopLoopbackOAuth {
  DesktopLoopbackOAuth({
    this.clientId =
        '87789110323-5picq501c0834g6g0amjqqpk628jaoh2.apps.googleusercontent.com',
    http.Client? httpClient,
  }) : _http = httpClient ?? http.Client();

  final String clientId;
  final http.Client _http;

  static bool get isNeeded {
    if (kIsWeb) return false;
    return Platform.isLinux || Platform.isWindows;
  }

  Future<DesktopOAuthResult> signIn({Duration timeout = const Duration(minutes: 3)}) async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final redirectUri = 'http://127.0.0.1:${server.port}/callback';
    final verifier = _codeVerifier();
    final challenge = _codeChallenge(verifier);
    final state = _randomUrlSafe(24);

    final authUrl = Uri.https('accounts.google.com', '/o/oauth2/v2/auth', {
      'client_id': clientId,
      'redirect_uri': redirectUri,
      'response_type': 'code',
      'scope': 'openid email profile',
      'code_challenge': challenge,
      'code_challenge_method': 'S256',
      'state': state,
      'access_type': 'offline',
      'prompt': 'select_account',
    });

    final completer = Completer<Map<String, String>>();
    late final StreamSubscription<HttpRequest> sub;
    sub = server.listen((request) async {
      if (request.uri.path != '/callback') {
        request.response.statusCode = 404;
        await request.response.close();
        return;
      }
      final params = request.uri.queryParameters;
      request.response.headers.contentType = ContentType.html;
      request.response.write(
        '<html><body><p>AURA sign-in complete. You can close this window.</p></body></html>',
      );
      await request.response.close();
      if (!completer.isCompleted) completer.complete(params);
      await sub.cancel();
      await server.close(force: true);
    });

    final launched = await launchUrl(authUrl, mode: LaunchMode.externalApplication);
    if (!launched) {
      await sub.cancel();
      await server.close(force: true);
      throw StateError('Could not open the system browser for Google sign-in.');
    }

    final params = await completer.future.timeout(timeout, onTimeout: () {
      sub.cancel();
      server.close(force: true);
      throw TimeoutException('Desktop Google sign-in timed out');
    });

    if (params['error'] != null) {
      throw StateError('Google sign-in error: ${params['error']}');
    }
    if (params['state'] != state) {
      throw StateError('OAuth state mismatch');
    }
    final code = params['code'];
    if (code == null || code.isEmpty) {
      throw StateError('OAuth callback missing code');
    }

    final tokenResponse = await _http.post(
      Uri.parse('https://oauth2.googleapis.com/token'),
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: {
        'client_id': clientId,
        'code': code,
        'code_verifier': verifier,
        'grant_type': 'authorization_code',
        'redirect_uri': redirectUri,
      },
    );
    if (tokenResponse.statusCode != 200) {
      throw StateError(
        'Token exchange failed (${tokenResponse.statusCode}): ${tokenResponse.body}',
      );
    }
    final body = jsonDecode(tokenResponse.body) as Map<String, dynamic>;
    final idToken = body['id_token'] as String?;
    if (idToken == null || idToken.isEmpty) {
      throw StateError('Token response missing id_token');
    }
    final profile = _profileFromIdToken(idToken);
    return DesktopOAuthResult(idToken: idToken, profile: profile);
  }

  Map<String, String> _profileFromIdToken(String idToken) {
    final parts = idToken.split('.');
    if (parts.length < 2) {
      return {'id': '', 'email': '', 'name': 'Desktop User'};
    }
    final normalized = base64Url.normalize(parts[1]);
    final payload =
        jsonDecode(utf8.decode(base64Url.decode(normalized))) as Map<String, dynamic>;
    return {
      'id': payload['sub']?.toString() ?? '',
      'email': payload['email']?.toString() ?? '',
      'name': payload['name']?.toString() ?? payload['email']?.toString() ?? 'Desktop User',
      'picture': payload['picture']?.toString() ?? '',
    };
  }

  String _codeVerifier() => _randomUrlSafe(64);

  String _codeChallenge(String verifier) {
    final digest = sha256.convert(utf8.encode(verifier));
    return base64UrlEncode(digest.bytes).replaceAll('=', '');
  }

  String _randomUrlSafe(int length) {
    final random = Random.secure();
    final bytes = List<int>.generate(length, (_) => random.nextInt(256));
    return base64UrlEncode(bytes).replaceAll('=', '');
  }
}

class DesktopOAuthResult {
  const DesktopOAuthResult({required this.idToken, required this.profile});
  final String idToken;
  final Map<String, String> profile;
}
