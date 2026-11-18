import 'dart:async';
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
  final Duration ingestTimeout;
  final Duration uploadTimeout;
  KnowledgeCenter(
    this._ref, {
    http.Client? httpClient,
    this.ingestTimeout = defaultIngestTimeout,
    this.uploadTimeout = defaultUploadTimeout,
  }) : _http = httpClient;

  /// `POST /knowledge/documents` chunks and embeds the whole document before it answers
  /// (`v3.py` `ingest_document`), so on the CPU-only VPS a large one takes far longer than the
  /// 10 s every POST used to get -- which reported a saved document as failed (INT-017).
  static const defaultIngestTimeout = Duration(seconds: 120);

  /// The PDF upload adds extraction and, when configured, OCR on top of that.
  static const defaultUploadTimeout = Duration(seconds: 180);

  /// Reads (list, CAG preview) and small writes (delete, pin).
  static const defaultRequestTimeout = Duration(seconds: 30);

  /// Search answers interactively; unchanged from before.
  static const searchTimeout = Duration(seconds: 10);

  /// The server's per-document limit (`KNOWLEDGE_MAX_DOC_BYTES`, default 8 MiB,
  /// `aura_core/config/settings.py`) as a fallback only: the client reads the real value from
  /// `/api/v3/capabilities` `knowledge.max_upload_bytes` before its first size check, and still
  /// adopts whatever `limit_bytes` a 422 reports. Used as-is only when neither is available (an
  /// older gateway, or the capabilities call failed).
  static const defaultMaxDocumentBytes = 8 * 1024 * 1024;

  /// The server's page-size ceiling for `GET /knowledge/documents` (`le=100`).
  static const listPageSize = 100;

  int _maxDocumentBytes = defaultMaxDocumentBytes;

  /// Set once `/capabilities` has answered with a limit, so it is asked at most once per
  /// successful read; a failed read is retried on the next size check.
  bool _limitFromServer = false;

  /// The byte limit this client checks before sending (INT-021).
  int get maxDocumentBytes => _maxDocumentBytes;

  String get _backendUrl => '${_ref.read(backendUrlProvider)}/api/v3/knowledge';

  /// Upper bound on the limit lookup. It runs in front of the write the user is waiting on, so a
  /// stalled server must not add more than this before that write's own timeout starts.
  static const capabilitiesTimeout = Duration(seconds: 3);

  /// Adopts the server's advertised upload limit (`knowledge.max_upload_bytes`), so a file the
  /// server accepts is never refused here because of a fixed client-side constant. Any failure
  /// keeps the current value: the server enforces the limit regardless. Waits at most
  /// [capabilitiesTimeout], or [operationTimeout] if that is shorter.
  Future<void> _ensureServerLimit(Duration operationTimeout) async {
    if (_limitFromServer) return;
    try {
      final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
      final headers = await _headers(token)..remove('Content-Type');
      final uri = Uri.parse('${_ref.read(backendUrlProvider)}/api/v3/capabilities');
      final client = _http;
      final response = await (client != null
              ? client.get(uri, headers: headers)
              : http.get(uri, headers: headers))
          .timeout(operationTimeout < capabilitiesTimeout ? operationTimeout : capabilitiesTimeout);
      if (response.statusCode != 200) {
        healActiveWorkspace(_ref, statusCode: response.statusCode, body: response.body);
        return;
      }
      final decoded = jsonDecode(response.body);
      final knowledge = decoded is Map<String, dynamic> ? decoded['knowledge'] : null;
      final limit = knowledge is Map<String, dynamic> ? knowledge['max_upload_bytes'] : null;
      if (limit is num && limit > 0) {
        _maxDocumentBytes = limit.toInt();
        _limitFromServer = true;
      }
    } catch (e) {
      debugPrint('KnowledgeCenter: upload limit unavailable, using $_maxDocumentBytes: $e');
    }
  }

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

  Future<http.Response> _post(
    String path, {
    required String body,
    required Map<String, String> headers,
    Duration timeout = defaultRequestTimeout,
  }) {
    final uri = Uri.parse("$_backendUrl$path");
    final client = _http;
    final sent = client != null
        ? client.post(uri, headers: headers, body: body)
        : http.post(uri, headers: headers, body: body);
    return sent.timeout(timeout);
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
          return (client != null
                  ? client.get(uri, headers: headers)
                  : http.get(uri, headers: headers))
              .timeout(defaultRequestTimeout);
        case 'DELETE':
          return (client != null
                  ? client.delete(uri, headers: headers)
                  : http.delete(uri, headers: headers))
              .timeout(defaultRequestTimeout);
        default:
          return _post(path, body: body ?? '{}', headers: headers);
      }
    }

    var response = await once();
    if (response.statusCode == 401) {
      tokens.invalidateAccessToken();
      response = await once();
    }
    // Every request here carries the active team: one we were removed from is dropped (F5).
    healActiveWorkspace(_ref, statusCode: response.statusCode, body: response.body);
    return response;
  }

  /// One 401 retry after dropping the cached access token, matching
  /// [SyncClient] / [SseAgentClient].
  Future<http.Response> _sendAuthenticated(
    String path,
    Map<String, dynamic> payload, {
    bool write = true,
    String? idempotencyKey,
    Duration timeout = defaultRequestTimeout,
  }) async {
    final tokens = _ref.read(tokenManagerProvider);
    // One key per logical write, shared by the 401 re-send below (audit FSA-046), and by a
    // caller's own retry of the same write when it passes one in (INT-017). A search is a POST
    // that writes nothing, so it carries none.
    final key = write ? (idempotencyKey ?? newIdempotencyKey()) : null;
    Future<http.Response> send() async {
      final token = await tokens.getValidAccessToken();
      final headers = await _headers(token);
      if (key != null) headers[idempotencyKeyHeader] = key;
      return _post(path, body: jsonEncode(payload), headers: headers, timeout: timeout);
    }

    var response = await send();
    if (response.statusCode == 401) {
      debugPrint("[KnowledgeCenter] 401 — invalidating access token and retrying once");
      tokens.invalidateAccessToken();
      response = await send();
    }
    healActiveWorkspace(_ref, statusCode: response.statusCode, body: response.body);
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
      final response = await _sendAuthenticated(
        "/documents",
        {
          "title": (title == null || title.isEmpty) ? docId : title,
          "content": content,
          "document_id": docId,
          "metadata": metadata ?? {},
        },
        timeout: ingestTimeout,
      );
      return response.statusCode == 200;
    } catch (e) {
      debugPrint("KnowledgeCenter: Ingestion failed: $e");
      return false;
    }
  }

  /// Queries the v3 knowledge index for matching passages.
  Future<List<Map<String, dynamic>>> queryKnowledge(String query, {int limit = 3}) async {
    try {
      final response = await _sendAuthenticated("/search", write: false, timeout: searchTimeout, {
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

  /// Ingests text, reporting why it failed when it did (INT-021).
  ///
  /// Pass the same [idempotencyKey] when retrying the same document after a result whose
  /// [KnowledgeIngestResult.retryWithSameKey] is true, so the server replays rather than
  /// saving it twice (INT-017).
  Future<KnowledgeIngestResult> ingestDocumentResult({
    required String title,
    required String content,
    String? documentId,
    String? idempotencyKey,
  }) async {
    await _ensureServerLimit(ingestTimeout);
    if (utf8.encode(content).length > _maxDocumentBytes) {
      return KnowledgeIngestResult.failed(
        KnowledgeIngestFailure.tooLarge,
        limitBytes: _maxDocumentBytes,
      );
    }
    try {
      final response = await _sendAuthenticated(
        "/documents",
        {
          "title": title,
          "content": content,
          if (documentId != null && documentId.isNotEmpty) "document_id": documentId,
          "metadata": {"source": "knowledge_center_ui"},
        },
        idempotencyKey: idempotencyKey,
        timeout: ingestTimeout,
      );
      return _ingestResultFrom(response);
    } on TimeoutException {
      debugPrint("KnowledgeCenter: ingestion timed out");
      return const KnowledgeIngestResult.failed(KnowledgeIngestFailure.timeout);
    } catch (e) {
      debugPrint("KnowledgeCenter: Ingestion failed: $e");
      return const KnowledgeIngestResult.failed(KnowledgeIngestFailure.network);
    }
  }

  /// Maps the gateway's answer to an ingest/upload result. Each status asks the user for
  /// something different, so none of them collapse into a generic failure (INT-021).
  KnowledgeIngestResult _ingestResultFrom(http.Response response) {
    final status = response.statusCode;
    if (status >= 200 && status < 300) {
      Map<String, dynamic> map;
      try {
        final parsed = jsonDecode(response.body);
        map = parsed is Map<String, dynamic> ? parsed : <String, dynamic>{};
      } catch (_) {
        map = <String, dynamic>{};
      }
      return KnowledgeIngestResult(
        ok: true,
        quarantinedChunks: map['quarantined_chunks'] is num
            ? (map['quarantined_chunks'] as num).toInt()
            : 0,
        skippedReason: map['skipped_reason']?.toString(),
        documentId: map['document_id']?.toString(),
      );
    }
    final error = AuraApiError.fromResponse(statusCode: status, body: response.body);
    final limit = error.details['limit_bytes'];
    if (limit is num || status == 413) {
      if (limit is num && limit > 0) _maxDocumentBytes = limit.toInt();
      return KnowledgeIngestResult.failed(
        KnowledgeIngestFailure.tooLarge,
        failureMessage: error.message,
        limitBytes: limit is num ? limit.toInt() : _maxDocumentBytes,
      );
    }
    final failure = switch (status) {
      401 => KnowledgeIngestFailure.unauthorized,
      403 => KnowledgeIngestFailure.forbidden,
      409 => KnowledgeIngestFailure.inProgress,
      429 => KnowledgeIngestFailure.rateLimited,
      503 => KnowledgeIngestFailure.unavailable,
      >= 500 => KnowledgeIngestFailure.server,
      _ => KnowledgeIngestFailure.rejected,
    };
    return KnowledgeIngestResult.failed(failure, failureMessage: error.message);
  }

  Future<KnowledgeSearchResult> searchKnowledge(String query, {int limit = 6}) async {
    try {
      final response = await _sendAuthenticated("/search", write: false, timeout: searchTimeout, {
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
    String? idempotencyKey,
  }) async {
    // The server reads the whole upload before refusing it (`upload_document`), so an
    // oversized file is checked here rather than sent (INT-021).
    await _ensureServerLimit(uploadTimeout);
    if (bytes.length > _maxDocumentBytes) {
      return KnowledgeIngestResult.failed(
        KnowledgeIngestFailure.tooLarge,
        limitBytes: _maxDocumentBytes,
      );
    }
    final tokens = _ref.read(tokenManagerProvider);
    // The route accepts an Idempotency-Key: one key for the 401 re-send and the caller's retry.
    final key = idempotencyKey ?? newIdempotencyKey();
    Future<http.StreamedResponse> send() async {
      final token = await tokens.getValidAccessToken();
      final uri = Uri.parse("$_backendUrl/documents/upload");
      final request = http.MultipartRequest('POST', uri)
        ..fields['title'] = title
        ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));
      if (token != null) request.headers['Authorization'] = 'Bearer $token';
      request.headers.addAll(await workspaceHeaders(_ref));
      request.headers[idempotencyKeyHeader] = key;
      request.headers.addAll(
        await DeviceValidationService.getAttestationHeaders(
          backendUrl: _ref.read(backendUrlProvider),
        ),
      );
      final client = _http;
      return client != null ? client.send(request) : http.Client().send(request);
    }

    Future<http.Response> exchange() async {
      var streamed = await send();
      if (streamed.statusCode == 401) {
        tokens.invalidateAccessToken();
        streamed = await send();
      }
      final response = await http.Response.fromStream(streamed);
      healActiveWorkspace(_ref, statusCode: response.statusCode, body: response.body);
      return response;
    }

    try {
      // It had no timeout at all: a stalled server left the upload spinner up for good (INT-017).
      return _ingestResultFrom(await exchange().timeout(uploadTimeout));
    } on TimeoutException {
      debugPrint("KnowledgeCenter: PDF upload timed out");
      return const KnowledgeIngestResult.failed(KnowledgeIngestFailure.timeout);
    } catch (e) {
      debugPrint("KnowledgeCenter: PDF upload failed: $e");
      return const KnowledgeIngestResult.failed(KnowledgeIngestFailure.network);
    }
  }

  /// Every active document, fetched a page at a time: the server returns at most 100 per call
  /// and 50 when no limit is given, so a library past 50 was cut off (INT-016).
  Future<List<KnowledgeDocument>> listDocuments() async {
    const maxPages = 1000; // a guard against a server that ignores offset
    final documents = <KnowledgeDocument>[];
    for (var page = 0; page < maxPages; page++) {
      final raw = await _listPage(limit: listPageSize, offset: page * listPageSize);
      documents.addAll(
        raw
            .map((item) => KnowledgeDocument.fromJson(Map<String, dynamic>.from(item)))
            .where((d) => d.id.isNotEmpty),
      );
      if (raw.length < listPageSize) break;
    }
    return documents;
  }

  Future<List<Map>> _listPage({required int limit, required int offset}) async {
    final response = await _send('GET', '/documents?limit=$limit&offset=$offset');
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw AuraApiError.fromResponse(
        statusCode: response.statusCode,
        body: response.body,
      );
    }
    final parsed = jsonDecode(response.body);
    final list = parsed is Map ? parsed['documents'] : parsed;
    if (list is! List) return const [];
    return list.whereType<Map>().toList();
  }

  Future<void> deleteDocument(String documentId) async {
    final response = await _send('DELETE', '/documents/$documentId');
    // 404 `not_found`: unknown, already deleted, or not ours (INT-111). Either way it is gone from
    // this caller's view, so a retried delete succeeds. A 403 (not a member) is still an error,
    // and `_send` has already healed the active team for it.
    if (response.statusCode == 404) return;
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
