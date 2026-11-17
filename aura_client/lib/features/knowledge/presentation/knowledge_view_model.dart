import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/ai/knowledge/knowledge_center.dart';
import '../../../core/network/aura_api_error.dart';
import '../domain/knowledge_document.dart';
import 'package:aura_client/l10n/l10n.dart';

class KnowledgeUiState {
  final bool loading;
  final String? error;
  final List<KnowledgeDocument> documents;
  final KnowledgeSearchResult? search;
  final String? ingestMessage;
  final bool ingestError;
  final CagPreview? cag;

  const KnowledgeUiState({
    this.loading = false,
    this.error,
    this.documents = const [],
    this.search,
    this.ingestMessage,
    this.ingestError = false,
    this.cag,
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
    CagPreview? cag,
  }) {
    return KnowledgeUiState(
      loading: loading ?? this.loading,
      error: clearError ? null : (error ?? this.error),
      documents: documents ?? this.documents,
      search: clearSearch ? null : (search ?? this.search),
      ingestMessage: clearIngest ? null : (ingestMessage ?? this.ingestMessage),
      ingestError: ingestError ?? this.ingestError,
      cag: cag ?? this.cag,
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
      final center = ref.read(knowledgeCenterProvider);
      final docs = await center.listDocuments();
      CagPreview? cag;
      try {
        cag = await center.fetchCagPreview();
      } catch (_) {
        cag = null;
      }
      state = state.copyWith(loading: false, documents: docs, cag: cag);
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
        ingestMessage: currentL10n.knowledgeAddFailed,
        ingestError: true,
      );
      return;
    }
    final quarantine = result.quarantinedChunks > 0
        ? ' ${currentL10n.knowledgePassagesHeldBack(result.quarantinedChunks)}'
        : '';
    state = state.copyWith(
      ingestMessage: '${currentL10n.knowledgeSaved}$quarantine',
      ingestError: false,
    );
    await refresh();
  }

  Future<void> uploadPdf({
    required String title,
    required List<int> bytes,
    String filename = 'document.pdf',
  }) async {
    final result = await ref.read(knowledgeCenterProvider).uploadPdf(
          title: title,
          bytes: bytes,
          filename: filename,
        );
    if (!result.ok) {
      state = state.copyWith(
        ingestMessage: currentL10n.knowledgePdfUploadFailed,
        ingestError: true,
      );
      return;
    }
    final quarantine = result.quarantinedChunks > 0
        ? ' ${currentL10n.knowledgePassagesHeldBack(result.quarantinedChunks)}'
        : '';
    state = state.copyWith(
      ingestMessage: '${currentL10n.knowledgeUploaded}$quarantine',
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

  Future<void> toggleCagPin(KnowledgeDocument doc) async {
    try {
      await ref.read(knowledgeCenterProvider).setCagPin(
            doc.id,
            pinned: !doc.cagPinned,
          );
      await refresh();
    } on AuraApiError catch (e) {
      state = state.copyWith(error: e.message);
    }
  }
}

final knowledgeViewModelProvider =
    NotifierProvider<KnowledgeViewModel, KnowledgeUiState>(KnowledgeViewModel.new);
