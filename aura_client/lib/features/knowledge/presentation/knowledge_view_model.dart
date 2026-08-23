import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/ai/knowledge/knowledge_center.dart';
import '../../../core/network/aura_api_error.dart';
import '../domain/knowledge_document.dart';

class KnowledgeUiState {
  final bool loading;
  final String? error;
  final List<KnowledgeDocument> documents;
  final KnowledgeSearchResult? search;
  final String? ingestMessage;
  final bool ingestError;

  const KnowledgeUiState({
    this.loading = false,
    this.error,
    this.documents = const [],
    this.search,
    this.ingestMessage,
    this.ingestError = false,
  });

  KnowledgeUiState copyWith({
    bool? loading,
    String? error,
    bool clearError = false,
    List<KnowledgeDocument>? documents,
    KnowledgeSearchResult? search,
    bool clearSearch = false,
    String? ingestMessage,
    bool clearIngest = false,
    bool? ingestError,
  }) {
    return KnowledgeUiState(
      loading: loading ?? this.loading,
      error: clearError ? null : (error ?? this.error),
      documents: documents ?? this.documents,
      search: clearSearch ? null : (search ?? this.search),
      ingestMessage: clearIngest ? null : (ingestMessage ?? this.ingestMessage),
      ingestError: ingestError ?? this.ingestError,
    );
  }
}

class KnowledgeViewModel extends Notifier<KnowledgeUiState> {
  @override
  KnowledgeUiState build() {
    Future.microtask(refresh);
    return const KnowledgeUiState(loading: true);
  }

  Future<void> refresh() async {
    state = state.copyWith(loading: true, clearError: true);
    try {
      final docs = await ref.read(knowledgeCenterProvider).listDocuments();
      state = state.copyWith(loading: false, documents: docs);
    } on AuraApiError catch (e) {
      state = state.copyWith(loading: false, error: e.message);
    } catch (e) {
      state = state.copyWith(loading: false, error: e.toString());
    }
  }

  Future<void> ingest({
    required String title,
    required String content,
    String? documentId,
  }) async {
    final result = await ref.read(knowledgeCenterProvider).ingestDocumentResult(
          title: title,
          content: content,
          documentId: documentId,
        );
    if (!result.ok) {
      state = state.copyWith(
        ingestMessage: 'Could not add this document.',
        ingestError: true,
      );
      return;
    }
    final quarantine = result.quarantinedChunks > 0
        ? ' ${result.quarantinedChunks} passage(s) were held back.'
        : '';
    state = state.copyWith(
      ingestMessage: 'Saved.$quarantine',
      ingestError: false,
    );
    await refresh();
  }

  Future<void> search(String query) async {
    final result = await ref.read(knowledgeCenterProvider).searchKnowledge(query);
    state = state.copyWith(search: result);
  }

  Future<void> remove(String documentId) async {
    await ref.read(knowledgeCenterProvider).deleteDocument(documentId);
    await refresh();
  }
}

final knowledgeViewModelProvider =
    NotifierProvider<KnowledgeViewModel, KnowledgeUiState>(KnowledgeViewModel.new);
