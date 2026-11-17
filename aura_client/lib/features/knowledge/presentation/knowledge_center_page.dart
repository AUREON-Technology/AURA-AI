import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import 'knowledge_view_model.dart';
import 'package:aura_client/l10n/l10n.dart';

class KnowledgeCenterPage extends ConsumerStatefulWidget {
  const KnowledgeCenterPage({super.key});

  @override
  ConsumerState<KnowledgeCenterPage> createState() => _KnowledgeCenterPageState();
}

class _KnowledgeCenterPageState extends ConsumerState<KnowledgeCenterPage> {
  final _title = TextEditingController();
  final _content = TextEditingController();
  final _search = TextEditingController();
  bool _uploading = false;

  @override
  void dispose() {
    _title.dispose();
    _content.dispose();
    _search.dispose();
    super.dispose();
  }

  /// Attaches a PDF and sends it to `/knowledge/documents/upload` -- scanned pages go through
  /// server-side OCR when the deployment has an engine configured (Phase F1). The title field
  /// doubles as the upload's title when filled in; otherwise the file name is used.
  Future<void> _pickAndUploadPdf() async {
    final picked = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['pdf'],
      withData: true,
    );
    final files = picked?.files ?? const <PlatformFile>[];
    if (files.isEmpty) return;
    final file = files.first;
    final bytes = file.bytes;
    if (bytes == null) {
      if (mounted) {
        AuraSnackbar.show(
          context,
          message: context.l10n.knowledgeFileReadFailed,
          kind: AuraSnackbarKind.error,
        );
      }
      return;
    }

    setState(() => _uploading = true);
    final title = _title.text.trim().isNotEmpty ? _title.text.trim() : file.name;
    await ref.read(knowledgeViewModelProvider.notifier).uploadPdf(
          title: title,
          bytes: bytes,
          filename: file.name,
        );
    if (mounted) setState(() => _uploading = false);
    _title.clear();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(knowledgeViewModelProvider);
    final palette = AuraPalette.of(context);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text(context.l10n.knowledgeTitle, style: Theme.of(context).textTheme.headlineMedium),
      ),
      body: ListView(
        padding: const EdgeInsets.all(AuraSpacing.lg),
        children: [
          AuraCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(context.l10n.knowledgeAddDocument, style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: AuraSpacing.md),
                AuraTextField(controller: _title, label: context.l10n.knowledgeTitleLabel),
                const SizedBox(height: AuraSpacing.md),
                AuraTextField(controller: _content, label: context.l10n.knowledgeContentLabel, maxLines: 5),
                const SizedBox(height: AuraSpacing.md),
                AuraButton(
                  label: context.l10n.commonSave,
                  expand: true,
                  onPressed: () {
                    final title = _title.text.trim();
                    final content = _content.text.trim();
                    if (title.isEmpty || content.isEmpty) {
                      AuraSnackbar.show(
                        context,
                        message: context.l10n.knowledgeTitleContentRequired,
                        kind: AuraSnackbarKind.error,
                      );
                      return;
                    }
                    ref.read(knowledgeViewModelProvider.notifier).ingest(
                          title: title,
                          content: content,
                        );
                    _title.clear();
                    _content.clear();
                  },
                ),
                const SizedBox(height: AuraSpacing.sm),
                AuraButton(
                  label: _uploading ? context.l10n.knowledgeUploading : context.l10n.knowledgeUploadPdfInstead,
                  icon: Icons.attach_file,
                  variant: AuraButtonVariant.secondary,
                  expand: true,
                  loading: _uploading,
                  onPressed: _uploading ? null : _pickAndUploadPdf,
                ),
                if (state.ingestMessage != null) ...[
                  const SizedBox(height: AuraSpacing.sm),
                  Text(
                    state.ingestMessage!,
                    style: TextStyle(
                      color: state.ingestError ? palette.danger : palette.success,
                    ),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: AuraSpacing.xl),
          AuraCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(context.l10n.commonSearch, style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: AuraSpacing.md),
                AuraTextField(
                  controller: _search,
                  label: context.l10n.knowledgeSearchLabel,
                  textInputAction: TextInputAction.search,
                  onSubmitted: (q) =>
                      ref.read(knowledgeViewModelProvider.notifier).search(q),
                ),
                const SizedBox(height: AuraSpacing.md),
                AuraButton(
                  label: context.l10n.commonSearch,
                  variant: AuraButtonVariant.secondary,
                  onPressed: () => ref
                      .read(knowledgeViewModelProvider.notifier)
                      .search(_search.text.trim()),
                ),
                if (state.search != null) ...[
                  const SizedBox(height: AuraSpacing.md),
                  if (state.search!.searchedButEmpty || state.search!.results.isEmpty)
                    Text(context.l10n.knowledgeSearchEmpty)
                  else
                    for (final hit in state.search!.results)
                      Padding(
                        padding: const EdgeInsets.only(bottom: AuraSpacing.sm),
                        child: Text(
                          (hit['citation'] ?? hit['content'] ?? '').toString(),
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ),
                ],
              ],
            ),
          ),
          const SizedBox(height: AuraSpacing.xl),
          if (state.cag != null && state.cag!.enabled)
            Padding(
              padding: const EdgeInsets.only(bottom: AuraSpacing.xl),
              child: AuraCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      context.l10n.knowledgeCagTitle,
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                    const SizedBox(height: AuraSpacing.sm),
                    Text(
                      '${context.l10n.knowledgeCagBudget(state.cag!.tokens, state.cag!.budget)}'
                      '${state.cag!.documents.isEmpty ? ' · ${context.l10n.knowledgeCagNothingPinned}' : ''}',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                    if (state.cag!.documents.isNotEmpty) ...[
                      const SizedBox(height: AuraSpacing.sm),
                      for (final d in state.cag!.documents)
                        Text(
                          '• ${d['title'] ?? d['document_id']} '
                          '(${d['is_global'] == true ? context.l10n.knowledgeCagHandbook : context.l10n.knowledgeCagYours}, '
                          '${context.l10n.knowledgeCagDocTokens('${d['tokens'] ?? 0}')})',
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                    ],
                  ],
                ),
              ),
            ),
          Text(context.l10n.knowledgeLibrary, style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: AuraSpacing.md),
          if (state.loading)
            const AuraSkeletonCard()
          else if (state.error != null)
            AuraEmptyState(
              icon: Icons.error_outline,
              title: context.l10n.knowledgeLoadFailed,
              message: state.error,
            )
          else if (state.documents.isEmpty)
            AuraEmptyState(
              icon: Icons.folder_open_outlined,
              title: context.l10n.knowledgeEmptyTitle,
              message: context.l10n.knowledgeEmptyMessage,
            )
          else
            for (final doc in state.documents)
              Padding(
                padding: const EdgeInsets.only(bottom: AuraSpacing.sm),
                child: AuraCard(
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(doc.title),
                    subtitle: Text(
                      'v${doc.version} · ${doc.status}'
                      '${doc.cagPinned ? ' · ${context.l10n.knowledgeAlwaysInContext}' : ''}',
                    ),
                    leading: doc.isQuarantined
                        ? Icon(Icons.shield_outlined, color: palette.warning)
                        : Icon(
                            doc.cagPinned
                                ? Icons.push_pin
                                : Icons.description_outlined,
                            color: doc.cagPinned
                                ? palette.brand
                                : palette.textSecondary,
                          ),
                    trailing: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        IconButton(
                          tooltip: doc.cagPinned
                              ? context.l10n.knowledgeUnpinTooltip
                              : context.l10n.knowledgePinTooltip,
                          icon: Icon(
                            doc.cagPinned
                                ? Icons.push_pin
                                : Icons.push_pin_outlined,
                          ),
                          onPressed: () => ref
                              .read(knowledgeViewModelProvider.notifier)
                              .toggleCagPin(doc),
                        ),
                        IconButton(
                          tooltip: context.l10n.commonDelete,
                          icon: const Icon(Icons.delete_outline),
                          onPressed: () => ref
                              .read(knowledgeViewModelProvider.notifier)
                              .remove(doc.id),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
        ],
      ),
    );
  }
}
