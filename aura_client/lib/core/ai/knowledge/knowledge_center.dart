import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../../config/config.dart';
import '../../auth/token_manager.dart';
import '../../security/device_validation_service.dart';

/// Client for the v3 knowledge APIs (`/api/v3/knowledge/*`). New writes go
/// to the kernel RAG index (the one Copilot retrieval uses). Legacy v1
/// `/api/rag/*` is locked (410) after the Phase 4 cutover.
class KnowledgeCenter {
  final Ref _ref;
  final http.Client? _http;
  static const String _backendUrl = "${AppConfig.backendUrl}/api/v3/knowledge";

  KnowledgeCenter(this._ref, {http.Client? httpClient}) : _http = httpClient;

  Future<Map<String, String>> _headers(String? token) async {
    final headers = <String, String>{"Content-Type": "application/json"};
    if (token != null) headers['Authorization'] = 'Bearer $token';
    headers.addAll(await DeviceValidationService.getAttestationHeaders());
    return headers;
  }

  Future<http.Response> _post(String path, {required String body, required Map<String, String> headers}) {
    final uri = Uri.parse("$_backendUrl$path");
    final client = _http;
    final sent = client != null
        ? client.post(uri, headers: headers, body: body)
        : http.post(uri, headers: headers, body: body);
    return sent.timeout(const Duration(seconds: 10));
  }

  /// One 401 retry after dropping the cached access token, matching
  /// [SyncClient] / [SseAgentClient].
  Future<http.Response> _sendAuthenticated(String path, Map<String, dynamic> payload) async {
    final tokens = _ref.read(tokenManagerProvider);
    Future<http.Response> send() async {
      final token = await tokens.getValidAccessToken();
      return _post(path, body: jsonEncode(payload), headers: await _headers(token));
    }

    var response = await send();
    if (response.statusCode == 401) {
      debugPrint("[KnowledgeCenter] 401 — invalidating access token and retrying once");
      tokens.invalidateAccessToken();
      response = await send();
    }
    return response;
  }

  /// Ingests string contents into the v3 vector store.
  Future<bool> ingestDocument({
    required String docId,
    required String content,
    Map<String, dynamic>? metadata,
    String? title,
  }) async {
    try {
      final response = await _sendAuthenticated("/documents", {
        "title": (title == null || title.isEmpty) ? docId : title,
        "content": content,
        "document_id": docId,
        "metadata": metadata ?? {},
      });
      return response.statusCode == 200;
    } catch (e) {
      debugPrint("KnowledgeCenter: Ingestion failed: $e");
      return false;
    }
  }

  /// Queries the v3 knowledge index for matching passages.
  Future<List<Map<String, dynamic>>> queryKnowledge(String query, {int limit = 3}) async {
    try {
      final response = await _sendAuthenticated("/search", {
        "query": query,
        "limit": limit,
      });
      if (response.statusCode != 200) return [];

      final parsed = jsonDecode(response.body);
      final results = parsed["results"] as List?;
      if (results == null) return [];
      return List<Map<String, dynamic>>.from(results);
    } catch (e) {
      debugPrint("KnowledgeCenter: Query failed: $e");
    }
    return [];
  }
}

final knowledgeCenterProvider = Provider<KnowledgeCenter>((ref) {
  return KnowledgeCenter(ref);
});
