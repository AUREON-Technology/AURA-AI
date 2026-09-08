import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../config/backend_url_provider.dart';
import 'token_manager.dart';

/// Client for ADR-011 desktop pairing (`/api/v3/auth/desktop/pairing`).
class DesktopPairingClient {
  DesktopPairingClient(this._ref, {http.Client? httpClient}) : _http = httpClient;

  final Ref _ref;
  final http.Client? _http;

  String get _base => '${_ref.read(backendUrlProvider)}/api/v3/auth/desktop';

  Future<http.Response> _post(String url, {Map<String, String>? headers, Object? body}) {
    final uri = Uri.parse(url);
    final client = _http;
    final sent = client != null
        ? client.post(uri, headers: headers, body: body)
        : http.post(uri, headers: headers, body: body);
    return sent.timeout(const Duration(seconds: 15));
  }

  /// Returns a one-time code the user types into `aura_hostd auth pair <code>`.
  Future<PairingCode> startPairing() async {
    final access = await _ref.read(tokenManagerProvider).getValidAccessToken();
    if (access == null) {
      throw StateError('Sign in before pairing the desktop daemon.');
    }
    final response = await _post(
      '$_base/pairing',
      headers: {
        'Authorization': 'Bearer $access',
        'Content-Type': 'application/json',
      },
    );
    if (response.statusCode != 200) {
      throw StateError('Pairing failed (${response.statusCode}): ${response.body}');
    }
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    return PairingCode(
      code: body['code'] as String,
      expiresIn: body['expires_in'] as int,
    );
  }
}

class PairingCode {
  const PairingCode({required this.code, required this.expiresIn});
  final String code;
  final int expiresIn;
}

final desktopPairingClientProvider = Provider<DesktopPairingClient>(
  (ref) => DesktopPairingClient(ref),
);
