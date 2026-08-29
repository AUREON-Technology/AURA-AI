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

class KnowledgeIngestResult {
  final bool ok;
  final int quarantinedChunks;
  final String? skippedReason;
  final String? documentId;

  const KnowledgeIngestResult({
    required this.ok,
    this.quarantinedChunks = 0,
    this.skippedReason,
    this.documentId,
  });
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
