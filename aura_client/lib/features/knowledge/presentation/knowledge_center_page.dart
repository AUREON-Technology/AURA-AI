import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import 'knowledge_view_model.dart';

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
          message: 'Could not read that file.',
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
        title: Text('Knowledge', style: Theme.of(context).textTheme.headlineMedium),
      ),
      body: ListView(
        padding: const EdgeInsets.all(AuraSpacing.lg),
        children: [
          AuraCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('Add a document', style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: AuraSpacing.md),
                AuraTextField(controller: _title, label: 'Title'),
                const SizedBox(height: AuraSpacing.md),
                AuraTextField(controller: _content, label: 'Content', maxLines: 5),
                const SizedBox(height: AuraSpacing.md),
                AuraButton(
                  label: 'Save',
                  expand: true,
                  onPressed: () {
                    final title = _title.text.trim();
                    final content = _content.text.trim();
                    if (title.isEmpty || content.isEmpty) {
                      AuraSnackbar.show(
                        context,
                        message: 'Title and content are required.',
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
                  label: _uploading ? 'Uploading…' : 'Upload a PDF instead',
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
                Text('Search', style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: AuraSpacing.md),
                AuraTextField(
                  controller: _search,
                  label: 'Look up a passage',
                  textInputAction: TextInputAction.search,
                  onSubmitted: (q) =>
                      ref.read(knowledgeViewModelProvider.notifier).search(q),
                ),
                const SizedBox(height: AuraSpacing.md),
                AuraButton(
                  label: 'Search',
                  variant: AuraButtonVariant.secondary,
                  onPressed: () => ref
                      .read(knowledgeViewModelProvider.notifier)
                      .search(_search.text.trim()),
                ),
                if (state.search != null) ...[
                  const SizedBox(height: AuraSpacing.md),
                  if (state.search!.searchedButEmpty || state.search!.results.isEmpty)
                    const Text('Searched your library — nothing relevant turned up.')
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
          Text('Library', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: AuraSpacing.md),
          if (state.loading)
            const AuraSkeletonCard()
          else if (state.error != null)
            AuraEmptyState(
              icon: Icons.error_outline,
              title: 'Could not load documents',
              message: state.error,
            )
          else if (state.documents.isEmpty)
            const AuraEmptyState(
              icon: Icons.folder_open_outlined,
              title: 'No documents yet',
              message: 'Save a title and some text to build your library.',
            )
          else
            for (final doc in state.documents)
              Padding(
                padding: const EdgeInsets.only(bottom: AuraSpacing.sm),
                child: AuraCard(
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(doc.title),
                    subtitle: Text('v${doc.version} · ${doc.status}'),
                    leading: doc.isQuarantined
                        ? Icon(Icons.shield_outlined, color: palette.warning)
                        : Icon(Icons.description_outlined, color: palette.textSecondary),
                    trailing: IconButton(
                      tooltip: 'Delete',
                      icon: const Icon(Icons.delete_outline),
                      onPressed: () => ref
                          .read(knowledgeViewModelProvider.notifier)
                          .remove(doc.id),
                    ),
                  ),
                ),
              ),
        ],
      ),
    );
  }
}
