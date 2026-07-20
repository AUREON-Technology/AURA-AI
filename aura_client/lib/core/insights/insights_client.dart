import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../auth/token_manager.dart';
import '../config/config.dart';
import '../security/device_validation_service.dart';

/// AURA Insights client (Doc/V2_BLUEPRINT.md §8) — talks to the backend's
/// `/api/v2/insights` endpoints. Additive feature: if Postgres isn't
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
  static const String _baseUrl = "${AppConfig.backendUrl}/api/v2/insights";

  InsightsClient(this._ref);

  Future<Map<String, String>> _headers() async {
    final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
    final headers = <String, String>{"Content-Type": "application/json"};
    if (token != null) headers["Authorization"] = "Bearer $token";
    headers.addAll(DeviceValidationService.getAttestationHeaders());
    return headers;
  }

  Future<List<AppInsight>> fetch() async {
    try {
      final response = await http.get(Uri.parse(_baseUrl), headers: await _headers()).timeout(const Duration(seconds: 10));
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
      final response = await http.post(Uri.parse("$_baseUrl/generate"), headers: await _headers()).timeout(const Duration(seconds: 15));
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
      await http.post(Uri.parse("$_baseUrl/$id/dismiss"), headers: await _headers()).timeout(const Duration(seconds: 10));
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
