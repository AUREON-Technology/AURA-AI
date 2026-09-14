import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../auth/token_manager.dart';
import '../config/backend_url_provider.dart';
import '../security/device_validation_service.dart';

/// D6 Part D: thin client for `POST /api/v3/complete`, the inline code-completion endpoint.
///
/// One-shot and side-effect-free (no session, no history), so unlike most `core/*` clients this
/// has no retry-on-401 loop and swallows every failure into `null` -- a dropped suggestion is a
/// no-op to the user, not something worth surfacing as an error banner mid-typing.
class InlineCompletionResult {
  const InlineCompletionResult({required this.text, required this.truncated});

  final String text;
  final bool truncated;
}

class InlineCompletionClient {
  final Ref _ref;
  InlineCompletionClient(this._ref);

  String get _url => '${_ref.read(backendUrlProvider)}/api/v3/complete';
  String get _backendUrl => _ref.read(backendUrlProvider);

  Future<Map<String, String>> _headers() async {
    final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
    final headers = <String, String>{"Content-Type": "application/json"};
    if (token != null) headers["Authorization"] = "Bearer $token";
    headers.addAll(
      await DeviceValidationService.getAttestationHeaders(backendUrl: _backendUrl),
    );
    return headers;
  }

  /// Returns `null` on any failure (network, auth, timeout, or a non-200 from the backend) --
  /// the caller drops the request silently rather than showing an error for a best-effort
  /// suggestion. `prefix`/`suffix` should already be sliced to the backend's caps (8000/4000
  /// chars) before calling this; the backend truncates on its own too, so an oversized value
  /// here would 422 rather than silently succeed.
  Future<InlineCompletionResult?> complete({
    required String prefix,
    required String suffix,
    String? language,
  }) async {
    try {
      final response = await http
          .post(
            Uri.parse(_url),
            headers: await _headers(),
            body: jsonEncode({
              'prefix': prefix,
              'suffix': suffix,
              'language': ?language,
            }),
          )
          // Above the backend's own 20s generation budget (see v3.py's `/complete` route) so a
          // slow-but-succeeding local model isn't cut off by the client first.
          .timeout(const Duration(seconds: 25));
      if (response.statusCode != 200) return null;
      final body = jsonDecode(response.body) as Map<String, dynamic>;
      final text = body['text'] as String? ?? '';
      if (text.isEmpty) return null;
      return InlineCompletionResult(text: text, truncated: body['truncated'] as bool? ?? false);
    } catch (e) {
      debugPrint('[InlineCompletionClient] complete failed: $e');
      return null;
    }
  }
}

final inlineCompletionClientProvider = Provider<InlineCompletionClient>((ref) => InlineCompletionClient(ref));
