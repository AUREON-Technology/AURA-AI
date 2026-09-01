import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';

import '../auth/token_manager.dart';
import '../config/backend_url_provider.dart';
import '../security/device_validation_service.dart';
import 'aura_api_error.dart';

/// Shared authenticated HTTP client for `/api/v3` JSON routes.
///
/// Attaches JWT, attestation, and `X-Correlation-Id`. Retries once on 401
/// after dropping the cached access token. Does not handle SSE — that stays
/// in [SseAgentClient].
class AuraApiClient {
  final Ref _ref;
  final http.Client? _http;
  final _uuid = const Uuid();

  AuraApiClient(this._ref, {http.Client? httpClient}) : _http = httpClient;

  String get _backendUrl => _ref.read(backendUrlProvider);

  Future<Map<String, String>> _headers({
    required bool jsonBody,
    String? correlationId,
  }) async {
    final headers = <String, String>{
      'X-Correlation-Id': correlationId ?? _uuid.v4(),
    };
    if (jsonBody) headers['Content-Type'] = 'application/json';
    final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
    if (token != null) headers['Authorization'] = 'Bearer $token';
    headers.addAll(
      await DeviceValidationService.getAttestationHeaders(backendUrl: _backendUrl),
    );
    return headers;
  }

  Future<http.Response> request({
    required String method,
    required String path,
    Map<String, dynamic>? body,
    String? correlationId,
  }) async {
    final uri = path.startsWith('http')
        ? Uri.parse(path)
        : Uri.parse('$_backendUrl$path');
    final tokens = _ref.read(tokenManagerProvider);

    Future<http.Response> send() async {
      final headers = await _headers(
        jsonBody: body != null,
        correlationId: correlationId,
      );
      final encoded = body == null ? null : jsonEncode(body);
      final client = _http;
      switch (method) {
        case 'GET':
          return client != null
              ? client.get(uri, headers: headers)
              : http.get(uri, headers: headers);
        case 'DELETE':
          return client != null
              ? client.delete(uri, headers: headers)
              : http.delete(uri, headers: headers);
        case 'POST':
          return client != null
              ? client.post(uri, headers: headers, body: encoded)
              : http.post(uri, headers: headers, body: encoded);
        default:
          throw ArgumentError('Unsupported method $method');
      }
    }

    try {
      var response = await send();
      if (response.statusCode == 401) {
        debugPrint('[AuraApiClient] 401 — invalidating access token and retrying once');
        tokens.invalidateAccessToken();
        response = await send();
      }
      return response;
    } catch (e) {
      throw AuraApiError.unreachable(e);
    }
  }

  Future<Map<String, dynamic>> json({
    required String method,
    required String path,
    Map<String, dynamic>? body,
    String? correlationId,
  }) async {
    final response = await request(
      method: method,
      path: path,
      body: body,
      correlationId: correlationId,
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw AuraApiError.fromResponse(
        statusCode: response.statusCode,
        body: response.body,
        retryAfterHeader: response.headers['retry-after'],
        correlationId: response.headers['x-correlation-id'],
      );
    }
    if (response.body.isEmpty) return const {};
    final decoded = jsonDecode(response.body);
    if (decoded is Map<String, dynamic>) return decoded;
    if (decoded is Map) {
      return decoded.map((k, v) => MapEntry(k.toString(), v));
    }
    return {'data': decoded};
  }

  Future<http.Response> get(String path) => request(method: 'GET', path: path);

  Future<http.Response> probeHealth() {
    final uri = Uri.parse('$_backendUrl/health/ready');
    final client = _http;
    return client != null ? client.get(uri) : http.get(uri);
  }
}

final auraApiClientProvider = Provider<AuraApiClient>((ref) {
  return AuraApiClient(ref);
});
