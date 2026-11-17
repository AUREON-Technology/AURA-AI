class KnowledgeDocument {
  final String id;
  final String title;
  final int version;
  final String status;
  final bool cagPinned;
  final DateTime? createdAt;

  const KnowledgeDocument({
    required this.id,
    required this.title,
    required this.version,
    required this.status,
    this.cagPinned = false,
    this.createdAt,
  });

  bool get isQuarantined => status.toLowerCase().contains('quarantine');

  factory KnowledgeDocument.fromJson(Map<String, dynamic> json) {
    return KnowledgeDocument(
      id: (json['document_id'] ?? json['id'] ?? '').toString(),
      title: (json['title'] ?? 'Untitled').toString(),
      version: json['version'] is num ? (json['version'] as num).toInt() : 1,
      status: (json['status'] ?? 'active').toString(),
      cagPinned: json['cag_pinned'] == true,
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? ''),
    );
  }
}

class KnowledgeSearchResult {
  final List<Map<String, dynamic>> results;
  final bool searchedButEmpty;

  const KnowledgeSearchResult({
    required this.results,
    required this.searchedButEmpty,
  });
}

/// Why an ingest or upload did not reach a 2xx answer (INT-021).
enum KnowledgeIngestFailure {
  /// Over the server's per-document byte limit (422 `details.limit_bytes`, or the client's
  /// own pre-check against it).
  tooLarge,

  /// 429 from the per-user or per-IP rate limiter.
  rateLimited,

  /// 503: a dependency (embedder, vector store) is down or the feature is off.
  unavailable,

  /// 409: the same Idempotency-Key is still being processed by an earlier attempt.
  inProgress,

  /// 401 after the one token refresh.
  unauthorized,

  /// 403: no knowledge-write capability.
  forbidden,

  /// Any other 4xx: the server read the request and refused it.
  rejected,

  /// No answer in time; the server may still have saved it.
  timeout,

  /// The request did not complete (no connection, reset).
  network,

  /// Any other 5xx.
  server,
}

class KnowledgeIngestResult {
  final bool ok;
  final int quarantinedChunks;
  final String? skippedReason;
  final String? documentId;

  /// Set when [ok] is false.
  final KnowledgeIngestFailure? failure;

  /// The server's own message, when it sent one.
  final String? failureMessage;

  /// The byte limit the server (or the client's pre-check) applied, for [KnowledgeIngestFailure.tooLarge].
  final int? limitBytes;

  const KnowledgeIngestResult({
    required this.ok,
    this.quarantinedChunks = 0,
    this.skippedReason,
    this.documentId,
    this.failure,
    this.failureMessage,
    this.limitBytes,
  });

  const KnowledgeIngestResult.failed(
    KnowledgeIngestFailure this.failure, {
    this.failureMessage,
    this.limitBytes,
  })  : ok = false,
        quarantinedChunks = 0,
        skippedReason = null,
        documentId = null;

  /// A retry of this same write should carry the same Idempotency-Key (INT-017): the server
  /// may already have stored it (timeout, network, 5xx), is still storing it (409), or refused
  /// it without storing anything (429, 503) -- in each case the server either replays the
  /// first answer or runs the write once. A definite refusal (422, 403) starts afresh.
  bool get retryWithSameKey => switch (failure) {
        KnowledgeIngestFailure.timeout ||
        KnowledgeIngestFailure.network ||
        KnowledgeIngestFailure.server ||
        KnowledgeIngestFailure.unavailable ||
        KnowledgeIngestFailure.rateLimited ||
        KnowledgeIngestFailure.inProgress =>
          true,
        _ => false,
      };
}

/// Snapshot of the caller's CAG prefix budget (ADR-010).
class CagPreview {
  final bool enabled;
  final int tokens;
  final int budget;
  final List<Map<String, dynamic>> documents;
  final String? identity;
  final String text;

  const CagPreview({
    required this.enabled,
    required this.tokens,
    required this.budget,
    this.documents = const [],
    this.identity,
    this.text = '',
  });

  factory CagPreview.fromJson(Map<String, dynamic> json) {
    final docs = json['documents'];
    return CagPreview(
      enabled: json['enabled'] != false,
      tokens: json['tokens'] is num ? (json['tokens'] as num).toInt() : 0,
      budget: json['budget'] is num ? (json['budget'] as num).toInt() : 0,
      documents: docs is List
          ? docs.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList()
          : const [],
      identity: json['identity']?.toString(),
      text: (json['text'] ?? '').toString(),
    );
  }
}
