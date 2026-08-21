import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/theme/theme.dart';
import 'package:aura_client/core/ai/knowledge/knowledge_center.dart';

class KnowledgeCenterPage extends ConsumerStatefulWidget {
  const KnowledgeCenterPage({super.key});

  @override
  ConsumerState<KnowledgeCenterPage> createState() => _KnowledgeCenterPageState();
}

class _LearningState {
  final bool isLoading;
  final String? message;
  final bool isError;

  _LearningState({required this.isLoading, this.message, this.isError = false});
}

class _KnowledgeCenterPageState extends ConsumerState<KnowledgeCenterPage> {
  final _docIdController = TextEditingController();
  final _contentController = TextEditingController();
  final _searchController = TextEditingController();

  _LearningState _ingestState = _LearningState(isLoading: false);
  _LearningState _searchState = _LearningState(isLoading: false);
  List<Map<String, dynamic>> _searchResults = [];

  Future<void> _ingestDoc() async {
    final docId = _docIdController.text.trim();
    final content = _contentController.text.trim();

    if (docId.isEmpty || content.isEmpty) {
      setState(() {
        _ingestState = _LearningState(
          isLoading: false,
          message: "Document ID and Content cannot be empty.",
          isError: true,
        );
      });
      return;
    }

    setState(() {
      _ingestState = _LearningState(isLoading: true);
    });

    final success = await ref.read(knowledgeCenterProvider).ingestDocument(
          docId: docId,
          content: content,
          metadata: {
            "source": "knowledge_center_ui",
            "timestamp": DateTime.now().toIso8601String(),
          },
        );

    if (success) {
      setState(() {
        _ingestState = _LearningState(
          isLoading: false,
          message: "Document successfully ingested and vectorized in Qdrant!",
        );
        _docIdController.clear();
        _contentController.clear();
      });
    } else {
      setState(() {
        _ingestState = _LearningState(
          isLoading: false,
          message: "Failed to ingest document. Verify backend connection.",
          isError: true,
        );
      });
    }
  }

  Future<void> _searchRAG() async {
    final query = _searchController.text.trim();
    if (query.isEmpty) return;

    setState(() {
      _searchState = _LearningState(isLoading: true);
      _searchResults = [];
    });

    final results = await ref.read(knowledgeCenterProvider).queryKnowledge(query);

    setState(() {
      _searchResults = results;
      _searchState = _LearningState(
        isLoading: false,
        message: results.isEmpty ? "No matching contexts found." : null,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AuraTheme.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: const Text(
          "RAG Knowledge Center",
          style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white),
        ),
      ),
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _buildIngestCard(),
              const SizedBox(height: 24),
              _buildSearchCard(),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildIngestCard() {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Row(
              children: [
                Icon(Icons.cloud_upload_outlined, color: AuraTheme.accentIndigo, size: 24),
                SizedBox(width: 12),
                Text(
                  "Ingest Document to Vector DB",
                  style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white),
                ),
              ],
            ),
            const SizedBox(height: 14),
            TextField(
              controller: _docIdController,
              decoration: const InputDecoration(
                labelText: "Document Unique ID (e.g. project_proposal_v1)",
                border: OutlineInputBorder(),
              ),
              style: const TextStyle(color: Colors.white, fontSize: 13),
            ),
            const SizedBox(height: 14),
            TextField(
              controller: _contentController,
              maxLines: 4,
              decoration: const InputDecoration(
                labelText: "Document Text / Context Content",
                border: OutlineInputBorder(),
              ),
              style: const TextStyle(color: Colors.white, fontSize: 13),
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: ElevatedButton(
                    onPressed: _ingestState.isLoading ? null : _ingestDoc,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AuraTheme.accentIndigo,
                    ),
                    child: _ingestState.isLoading
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                          )
                        : const Text("Vectorize & Ingest"),
                  ),
                ),
              ],
            ),
            if (_ingestState.message != null) ...[
              const SizedBox(height: 12),
              Center(
                child: Text(
                  _ingestState.message!,
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: _ingestState.isError ? Colors.redAccent : AuraTheme.accentTeal,
                    fontSize: 12,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
            ]
          ],
        ),
      ),
    );
  }

  Widget _buildSearchCard() {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Row(
              children: [
                Icon(Icons.saved_search, color: AuraTheme.accentAmber, size: 24),
                SizedBox(width: 12),
                Text(
                  "Query Knowledge Context",
                  style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white),
                ),
              ],
            ),
            const SizedBox(height: 14),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _searchController,
                    decoration: const InputDecoration(
                      labelText: "Search knowledge base...",
                      border: OutlineInputBorder(),
                    ),
                    style: const TextStyle(color: Colors.white, fontSize: 13),
                    onSubmitted: (_) => _searchRAG(),
                  ),
                ),
                const SizedBox(width: 12),
                ElevatedButton(
                  onPressed: _searchState.isLoading ? null : _searchRAG,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AuraTheme.accentAmber,
                    padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
                  ),
                  child: _searchState.isLoading
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2, color: Colors.black),
                        )
                      : const Icon(Icons.search, color: Colors.black),
                )
              ],
            ),
            if (_searchState.message != null) ...[
              const SizedBox(height: 12),
              Text(
                _searchState.message!,
                style: const TextStyle(color: Colors.white54, fontSize: 12),
              ),
            ],
            if (_searchResults.isNotEmpty) ...[
              const SizedBox(height: 16),
              const Text(
                "Matching Contexts",
                style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white70, fontSize: 13),
              ),
              const SizedBox(height: 8),
              ListView.builder(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                itemCount: _searchResults.length,
                itemBuilder: (context, index) {
                  final result = _searchResults[index];
                  // Context matches list
                  final content = result["content"] ?? result["text"] ?? "";
                  final score = result["score"] ?? 0.0;
                  final docId = result["metadata"]?["doc_id"] ?? "unknown_doc";

                  return Container(
                    margin: const EdgeInsets.only(bottom: 10),
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: Colors.black26,
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: Colors.white10),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Text(
                              "Doc: $docId",
                              style: const TextStyle(color: AuraTheme.accentAmber, fontSize: 11, fontWeight: FontWeight.bold),
                            ),
                            Text(
                              "Score: ${(score * 100).toStringAsFixed(1)}%",
                              style: const TextStyle(color: AuraTheme.accentTeal, fontSize: 11, fontWeight: FontWeight.bold),
                            ),
                          ],
                        ),
                        const SizedBox(height: 6),
                        Text(
                          content,
                          style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 12),
                        ),
                      ],
                    ),
                  );
                },
              )
            ]
          ],
        ),
      ),
    );
  }
}
