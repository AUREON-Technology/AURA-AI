import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../../config/config.dart';
import '../../../features/auth/presentation/auth_provider.dart';
import '../../security/device_validation_service.dart';

class KnowledgeCenter {
  final Ref _ref;
  static const String _backendUrl = "${AppConfig.backendUrl}/api/rag";

  KnowledgeCenter(this._ref);

  /// Ingests string contents into the remote vector store
  Future<bool> ingestDocument({
    required String docId,
    required String content,
    Map<String, dynamic>? metadata,
  }) async {
    try {
      final token = await _ref.read(authProvider.notifier).getIdToken();
      final headers = {"Content-Type": "application/json"};
      if (token != null) {
        headers['Authorization'] = 'Bearer $token';
      }
      headers.addAll(DeviceValidationService.getAttestationHeaders());

      final response = await http.post(
        Uri.parse("$_backendUrl/ingest"),
        headers: headers,
        body: jsonEncode({
          "doc_id": docId,
          "content": content,
          "metadata": metadata ?? {},
        }),
      ).timeout(const Duration(seconds: 10));

      return response.statusCode == 200;
    } catch (e) {
      debugPrint("KnowledgeCenter: Ingestion failed: $e");
      return false;
    }
  }

  /// Queries the vector database for matching passages
  Future<List<Map<String, dynamic>>> queryKnowledge(String query, {int limit = 3}) async {
    try {
      final token = await _ref.read(authProvider.notifier).getIdToken();
      final headers = {"Content-Type": "application/json"};
      if (token != null) {
        headers['Authorization'] = 'Bearer $token';
      }
      headers.addAll(DeviceValidationService.getAttestationHeaders());

      final response = await http.post(
        Uri.parse("$_backendUrl/query"),
        headers: headers,
        body: jsonEncode({
          "query": query,
          "limit": limit,
        }),
      ).timeout(const Duration(seconds: 10));

      if (response.statusCode == 200) {
        final parsed = jsonDecode(response.body);
        final results = parsed["results"] as List?;
        if (results != null) {
          return List<Map<String, dynamic>>.from(results);
        }
      }
    } catch (e) {
      debugPrint("KnowledgeCenter: Query failed: $e");
    }
    return [];
  }
}

final knowledgeCenterProvider = Provider<KnowledgeCenter>((ref) {
  return KnowledgeCenter(ref);
});
