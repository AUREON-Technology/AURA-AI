import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../auth/token_manager.dart';
import '../config/backend_url_provider.dart';
import '../platform/device_timezone.dart';
import '../security/device_validation_service.dart';

/// AURA Insights client (Doc/V2_BLUEPRINT.md §8) — talks to the backend's
/// `/api/v3/insights` endpoints. Additive feature: if Postgres isn't
/// configured on the backend the endpoints return 503, which this client
/// treats the same as "no insights right now" rather than surfacing an
/// error, so the Today screen degrades gracefully on deployments that
/// haven't set DATABASE_URL yet.
class AppInsight {
  final String id;
  final String kind;
  final String message;
  final String explanation;
  final DateTime createdAt;

  const AppInsight({
    required this.id,
    required this.kind,
    required this.message,
    required this.explanation,
    required this.createdAt,
  });

  factory AppInsight.fromJson(Map<String, dynamic> json) => AppInsight(
        id: json['id'] as String,
        kind: json['kind'] as String,
        message: json['message'] as String,
        explanation: json['explanation'] as String,
        createdAt: DateTime.parse(json['created_at'] as String),
      );
}

class InsightsClient {
  final Ref _ref;
  final http.Client? _http;
  final DeviceTimezoneLookup _timezone;
  InsightsClient(this._ref, {http.Client? httpClient, DeviceTimezoneLookup? timezone})
      : _http = httpClient,
        _timezone = timezone ?? deviceTimezone;

  String get _baseUrl => '${_ref.read(backendUrlProvider)}/api/v3/insights';
  String get _backendUrl => _ref.read(backendUrlProvider);

  Future<Map<String, String>> _headers() async {
    final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
    final headers = <String, String>{"Content-Type": "application/json"};
    if (token != null) headers["Authorization"] = "Bearer $token";
    headers.addAll(
      await DeviceValidationService.getAttestationHeaders(backendUrl: _backendUrl),
    );
    // The backend stores it, so its hourly and 06:00 runs know where this user's day starts.
    final zone = await _timezone();
    if (zone != null) headers[clientTimezoneHeader] = zone;
    return headers;
  }

  Future<http.Response> _get(Uri uri, Map<String, String> headers) {
    final client = _http;
    return client != null ? client.get(uri, headers: headers) : http.get(uri, headers: headers);
  }

  Future<http.Response> _post(Uri uri, Map<String, String> headers) {
    final client = _http;
    return client != null ? client.post(uri, headers: headers) : http.post(uri, headers: headers);
  }

  Future<http.Response> _send(Future<http.Response> Function() send) async {
    var response = await send();
    if (response.statusCode == 401) {
      _ref.read(tokenManagerProvider).invalidateAccessToken();
      response = await send();
    }
    return response;
  }

  Future<List<AppInsight>> fetch() async {
    try {
      final response = await _send(() async {
        return _get(Uri.parse(_baseUrl), await _headers()).timeout(const Duration(seconds: 10));
      });
      if (response.statusCode != 200) return const [];
      final List list = jsonDecode(response.body);
      return list.map((e) => AppInsight.fromJson(e as Map<String, dynamic>)).toList();
    } catch (e) {
      debugPrint("[InsightsClient] fetch failed: $e");
      return const [];
    }
  }

  Future<List<AppInsight>> generate() async {
    try {
      final response = await _send(() async {
        return _post(Uri.parse("$_baseUrl/generate"), await _headers()).timeout(const Duration(seconds: 15));
      });
      if (response.statusCode != 200) return const [];
      final List list = jsonDecode(response.body);
      return list.map((e) => AppInsight.fromJson(e as Map<String, dynamic>)).toList();
    } catch (e) {
      debugPrint("[InsightsClient] generate failed: $e");
      return const [];
    }
  }

  Future<void> dismiss(String id) async {
    try {
      await _post(Uri.parse("$_baseUrl/$id/dismiss"), await _headers()).timeout(const Duration(seconds: 10));
    } catch (e) {
      debugPrint("[InsightsClient] dismiss failed: $e");
    }
  }
}

final insightsClientProvider = Provider<InsightsClient>((ref) => InsightsClient(ref));

/// Fetches on first load, then triggers a fresh generation in the
/// background so the list reflects today's data rather than a stale
/// scheduled-job snapshot — cheap since the rule-based analysis in
/// services/insights.py has no external API dependency.
final insightsProvider = FutureProvider.autoDispose<List<AppInsight>>((ref) async {
  final client = ref.watch(insightsClientProvider);
  final existing = await client.fetch();
  if (existing.isNotEmpty) return existing;
  return client.generate();
});
