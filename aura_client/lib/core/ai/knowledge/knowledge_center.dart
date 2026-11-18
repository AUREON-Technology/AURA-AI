import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:aura_client/core/network/idempotency.dart';
import '../../config/backend_url_provider.dart';
import '../../auth/token_manager.dart';
import '../../network/aura_api_error.dart';
import '../../security/device_validation_service.dart';
import '../../../features/knowledge/domain/knowledge_document.dart';

/// Client for the v3 knowledge APIs (`/api/v3/knowledge/*`). New writes go
/// to the kernel RAG index (the one Copilot retrieval uses). Legacy v1
/// `/api/rag/*` is locked (410) after the Phase 4 cutover.
class KnowledgeCenter {
  final Ref _ref;
  final http.Client? _http;
  KnowledgeCenter(this._ref, {http.Client? httpClient}) : _http = httpClient;

  String get _backendUrl => '${_ref.read(backendUrlProvider)}/api/v3/knowledge';

  Future<Map<String, String>> _headers(String? token) async {
    final headers = <String, String>{"Content-Type": "application/json"};
    if (token != null) headers['Authorization'] = 'Bearer $token';
    headers.addAll(
      await DeviceValidationService.getAttestationHeaders(
        backendUrl: _ref.read(backendUrlProvider),
      ),
    );
    // In a team, documents belong to the team's corpus (ADR-034).
    headers.addAll(await workspaceHeaders(_ref));
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

  Future<http.Response> _send(
    String method,
    String path, {
    String? body,
  }) async {
    final tokens = _ref.read(tokenManagerProvider);
    // One key per logical write, shared by the 401 re-send below (audit FSA-046).
    final idempotencyKey = isSideEffecting(method) ? newIdempotencyKey() : null;
    Future<http.Response> once() async {
      final token = await tokens.getValidAccessToken();
      final headers = await _headers(token);
      if (body == null) headers.remove('Content-Type');
      if (idempotencyKey != null) headers[idempotencyKeyHeader] = idempotencyKey;
      final uri = Uri.parse("$_backendUrl$path");
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
        default:
          return _post(path, body: body ?? '{}', headers: headers);
      }
    }

    var response = await once();
    if (response.statusCode == 401) {
      tokens.invalidateAccessToken();
      response = await once();
    }
    return response;
  }

  /// One 401 retry after dropping the cached access token, matching
  /// [SyncClient] / [SseAgentClient].
  Future<http.Response> _sendAuthenticated(
    String path,
    Map<String, dynamic> payload, {
    bool write = true,
  }) async {
    final tokens = _ref.read(tokenManagerProvider);
    // One key per logical write, shared by the 401 re-send below (audit FSA-046). A search is
    // a POST that writes nothing, so it carries none.
    final idempotencyKey = write ? newIdempotencyKey() : null;
    Future<http.Response> send() async {
      final token = await tokens.getValidAccessToken();
      final headers = await _headers(token);
      if (idempotencyKey != null) headers[idempotencyKeyHeader] = idempotencyKey;
      return _post(path, body: jsonEncode(payload), headers: headers);
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
      final response = await _sendAuthenticated("/search", write: false, {
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

  Future<KnowledgeIngestResult> ingestDocumentResult({
    required String title,
    required String content,
    String? documentId,
  }) async {
    try {
      final response = await _sendAuthenticated("/documents", {
        "title": title,
        "content": content,
        if (documentId != null && documentId.isNotEmpty) "document_id": documentId,
        "metadata": {"source": "knowledge_center_ui"},
      });
      if (response.statusCode < 200 || response.statusCode >= 300) {
        return const KnowledgeIngestResult(ok: false);
      }
      final parsed = jsonDecode(response.body);
      final map = parsed is Map<String, dynamic> ? parsed : <String, dynamic>{};
      return KnowledgeIngestResult(
        ok: true,
        quarantinedChunks: map['quarantined_chunks'] is num
            ? (map['quarantined_chunks'] as num).toInt()
            : 0,
        skippedReason: map['skipped_reason']?.toString(),
        documentId: map['document_id']?.toString(),
      );
    } catch (e) {
      debugPrint("KnowledgeCenter: Ingestion failed: $e");
      return const KnowledgeIngestResult(ok: false);
    }
  }

  Future<KnowledgeSearchResult> searchKnowledge(String query, {int limit = 6}) async {
    try {
      final response = await _sendAuthenticated("/search", write: false, {
        "query": query,
        "limit": limit,
      });
      if (response.statusCode != 200) {
        return const KnowledgeSearchResult(results: [], searchedButEmpty: false);
      }
      final parsed = jsonDecode(response.body);
      final results = parsed["results"] as List?;
      return KnowledgeSearchResult(
        results: results == null ? const [] : List<Map<String, dynamic>>.from(results),
        searchedButEmpty: parsed["searched_but_empty"] == true,
      );
    } catch (e) {
      debugPrint("KnowledgeCenter: Query failed: $e");
      return const KnowledgeSearchResult(results: [], searchedButEmpty: false);
    }
  }

  /// Uploads a raw PDF to `/documents/upload` (Phase F1) -- text and scanned pages alike, with
  /// OCR applied server-side when the deployment has an OCR engine configured. Unlike
  /// [ingestDocumentResult], this sends bytes as multipart form data, not JSON, so it builds its
  /// own request rather than going through [_sendAuthenticated].
  Future<KnowledgeIngestResult> uploadPdf({
    required String title,
    required List<int> bytes,
    String filename = 'document.pdf',
  }) async {
    final tokens = _ref.read(tokenManagerProvider);
    Future<http.StreamedResponse> send() async {
      final token = await tokens.getValidAccessToken();
      final uri = Uri.parse("$_backendUrl/documents/upload");
      final request = http.MultipartRequest('POST', uri)
        ..fields['title'] = title
        ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));
      if (token != null) request.headers['Authorization'] = 'Bearer $token';
      request.headers.addAll(await workspaceHeaders(_ref));
      request.headers.addAll(
        await DeviceValidationService.getAttestationHeaders(
          backendUrl: _ref.read(backendUrlProvider),
        ),
      );
      final client = _http;
      return client != null ? client.send(request) : http.Client().send(request);
    }

    try {
      var streamed = await send();
      if (streamed.statusCode == 401) {
        tokens.invalidateAccessToken();
        streamed = await send();
      }
      final response = await http.Response.fromStream(streamed);
      if (response.statusCode < 200 || response.statusCode >= 300) {
        return const KnowledgeIngestResult(ok: false);
      }
      final parsed = jsonDecode(response.body);
      final map = parsed is Map<String, dynamic> ? parsed : <String, dynamic>{};
      return KnowledgeIngestResult(
        ok: true,
        quarantinedChunks: map['quarantined_chunks'] is num
            ? (map['quarantined_chunks'] as num).toInt()
            : 0,
        skippedReason: map['skipped_reason']?.toString(),
        documentId: map['document_id']?.toString(),
      );
    } catch (e) {
      debugPrint("KnowledgeCenter: PDF upload failed: $e");
      return const KnowledgeIngestResult(ok: false);
    }
  }

  Future<List<KnowledgeDocument>> listDocuments() async {
    final response = await _send('GET', '/documents');
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw AuraApiError.fromResponse(
        statusCode: response.statusCode,
        body: response.body,
      );
    }
    final parsed = jsonDecode(response.body);
    final list = parsed is Map ? parsed['documents'] : parsed;
    if (list is! List) return const [];
    return list
        .whereType<Map>()
        .map((item) => KnowledgeDocument.fromJson(Map<String, dynamic>.from(item)))
        .where((d) => d.id.isNotEmpty)
        .toList();
  }

  Future<void> deleteDocument(String documentId) async {
    final response = await _send('DELETE', '/documents/$documentId');
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw AuraApiError.fromResponse(
        statusCode: response.statusCode,
        body: response.body,
      );
    }
  }

  /// Pin a document into the always-on CAG prefix (ADR-010).
  Future<void> setCagPin(String documentId, {required bool pinned}) async {
    final response = await _send(
      pinned ? 'POST' : 'DELETE',
      '/documents/$documentId/cag-pin',
    );
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw AuraApiError.fromResponse(
        statusCode: response.statusCode,
        body: response.body,
      );
    }
  }

  Future<CagPreview> fetchCagPreview() async {
    final response = await _send('GET', '/cag');
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw AuraApiError.fromResponse(
        statusCode: response.statusCode,
        body: response.body,
      );
    }
    final parsed = jsonDecode(response.body);
    if (parsed is! Map) {
      return const CagPreview(enabled: false, tokens: 0, budget: 0);
    }
    return CagPreview.fromJson(Map<String, dynamic>.from(parsed));
  }
}

final knowledgeCenterProvider = Provider<KnowledgeCenter>((ref) {
  return KnowledgeCenter(ref);
});
