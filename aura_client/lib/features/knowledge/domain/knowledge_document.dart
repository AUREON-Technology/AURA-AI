class KnowledgeDocument {
  final String id;
  final String title;
  final int version;
  final String status;
  final DateTime? createdAt;

  const KnowledgeDocument({
    required this.id,
    required this.title,
    required this.version,
    required this.status,
    this.createdAt,
  });

  bool get isQuarantined => status.toLowerCase().contains('quarantine');

  factory KnowledgeDocument.fromJson(Map<String, dynamic> json) {
    return KnowledgeDocument(
      id: (json['document_id'] ?? json['id'] ?? '').toString(),
      title: (json['title'] ?? 'Untitled').toString(),
      version: json['version'] is num ? (json['version'] as num).toInt() : 1,
      status: (json['status'] ?? 'active').toString(),
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
