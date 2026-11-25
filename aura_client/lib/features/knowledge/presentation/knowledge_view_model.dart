import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/ai/knowledge/knowledge_center.dart';
import '../../../core/network/aura_api_error.dart';
import '../../../core/network/idempotency.dart';
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

/// A write whose outcome is not settled, kept so a retry of the *same* content reuses its
/// Idempotency-Key and the server replays rather than saving twice (INT-017).
class _PendingWrite {
  const _PendingWrite(this.fingerprint, this.key);
  final String fingerprint;
  final String key;
}

class KnowledgeViewModel extends Notifier<KnowledgeUiState> {
  _PendingWrite? _pendingIngest;
  _PendingWrite? _pendingUpload;

  /// The key for this write: the pending one when [fingerprint] matches it, else a new one.
  static _PendingWrite _keyFor(_PendingWrite? pending, String fingerprint) {
    if (pending != null && pending.fingerprint == fingerprint) return pending;
    return _PendingWrite(fingerprint, newIdempotencyKey());
  }

  static String _fingerprint(List<Object?> parts, List<int> body) {
    final digest = sha256.convert(body).toString();
    return [...parts.map((p) => p?.toString() ?? ''), digest].join('\u0000');
  }

  /// The message for an ingest or upload that did not succeed (INT-021).
  String _failureMessage(KnowledgeIngestResult result, {required bool pdf}) {
    // In the language on screen (docs/plans/arabic-language_plan.md); `kind` selects the
    // document/PDF wording inside each message.
    final l10n = currentL10n;
    final kind = pdf ? 'pdf' : 'document';
    final limit = result.limitBytes ?? ref.read(knowledgeCenterProvider).maxDocumentBytes;
    return switch (result.failure) {
      KnowledgeIngestFailure.tooLarge => l10n.knowledgeErrTooLarge(kind, _megabytes(limit)),
      KnowledgeIngestFailure.rateLimited => l10n.knowledgeErrRateLimited,
      KnowledgeIngestFailure.unavailable => l10n.knowledgeErrUnavailable,
      KnowledgeIngestFailure.inProgress => l10n.knowledgeErrInProgress(kind),
      KnowledgeIngestFailure.timeout => l10n.knowledgeErrTimeout(kind),
      KnowledgeIngestFailure.network => l10n.knowledgeErrNetwork,
      KnowledgeIngestFailure.unauthorized => l10n.knowledgeErrSignIn,
      KnowledgeIngestFailure.forbidden => l10n.knowledgeErrForbidden,
      KnowledgeIngestFailure.server => l10n.knowledgeErrServer(kind),
      KnowledgeIngestFailure.rejected || null =>
        (result.failureMessage?.isNotEmpty ?? false)
            ? l10n.knowledgeErrRejected(kind, result.failureMessage!)
            : (pdf ? l10n.knowledgePdfUploadFailed : l10n.knowledgeAddFailed),
    };
  }

  static String _megabytes(int bytes) {
    final mb = bytes / (1024 * 1024);
    return mb == mb.roundToDouble() ? '${mb.round()} MB' : '${mb.toStringAsFixed(1)} MB';
  }

  /// Reports a 2xx outcome; returns whether the library changed and should be reloaded.
  bool _reportSaved(KnowledgeIngestResult result, {required bool pdf}) {
    final l10n = currentL10n;
    switch (result.skippedReason) {
      case null || '':
        final saved = pdf ? l10n.knowledgeUploaded : l10n.knowledgeSaved;
        final quarantine = result.quarantinedChunks > 0
            ? ' ${l10n.knowledgePassagesHeldBack(result.quarantinedChunks)}'
            : '';
        // Stored, but the server's index is not ready, so search cannot find it yet. Saying so
        // is the point: "Saved." alone read as done while every query came back empty.
        final notSearchable = result.searchable == false ? ' ${l10n.knowledgeNotSearchableYet}' : '';
        state = state.copyWith(
          ingestMessage: '$saved$quarantine$notSearchable',
          ingestError: false,
        );
        return true;
      case 'unchanged_content':
        state = state.copyWith(ingestMessage: l10n.knowledgeAlreadyUnchanged, ingestError: false);
        return false;
      case 'fully_quarantined':
        state = state.copyWith(ingestMessage: l10n.knowledgeNotSavedQuarantined, ingestError: true);
        return false;
      default:
        state = state.copyWith(
          ingestMessage: l10n.knowledgeNotSavedReason(result.skippedReason!),
          ingestError: true,
        );
        return false;
    }
  }

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
    final pending = _keyFor(
      _pendingIngest,
      _fingerprint(['text', title, documentId], utf8.encode(content)),
    );
    final result = await ref.read(knowledgeCenterProvider).ingestDocumentResult(
          title: title,
          content: content,
          documentId: documentId,
          idempotencyKey: pending.key,
        );
    _pendingIngest = !result.ok && result.retryWithSameKey ? pending : null;
    if (!result.ok) {
      state = state.copyWith(
        ingestMessage: _failureMessage(result, pdf: false),
        ingestError: true,
      );
      return;
    }
    if (_reportSaved(result, pdf: false)) await refresh();
  }

  Future<void> uploadPdf({
    required String title,
    required List<int> bytes,
    String filename = 'document.pdf',
  }) async {
    final pending = _keyFor(_pendingUpload, _fingerprint(['pdf', title, filename], bytes));
    final result = await ref.read(knowledgeCenterProvider).uploadPdf(
          title: title,
          bytes: bytes,
          filename: filename,
          idempotencyKey: pending.key,
        );
    _pendingUpload = !result.ok && result.retryWithSameKey ? pending : null;
    if (!result.ok) {
      state = state.copyWith(
        ingestMessage: _failureMessage(result, pdf: true),
        ingestError: true,
      );
      return;
    }
    if (_reportSaved(result, pdf: true)) await refresh();
  }

  Future<void> search(String query) async {
    final result = await ref.read(knowledgeCenterProvider).searchKnowledge(query);
    state = state.copyWith(search: result);
  }

  /// Deletes a document. A failure is reported in the action banner and the list stays as
  /// it was; it used to throw out of the button's callback (INT-021).
  Future<void> remove(String documentId) async {
    try {
      await ref.read(knowledgeCenterProvider).deleteDocument(documentId);
    } on AuraApiError catch (e) {
      state = state.copyWith(
        ingestMessage: currentL10n.knowledgeDeleteFailedReason(e.message),
        ingestError: true,
      );
      return;
    } catch (_) {
      state = state.copyWith(
        ingestMessage: currentL10n.knowledgeDeleteUnreachable,
        ingestError: true,
      );
      return;
    }
    state = state.copyWith(clearIngest: true);
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
