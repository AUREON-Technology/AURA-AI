import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';
import 'package:aura_client/l10n/l10n.dart';

import 'memory_view_model.dart';

class MemoryPage extends ConsumerWidget {
  const MemoryPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(memoryViewModelProvider);
    final palette = AuraPalette.of(context);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text(context.l10n.memoryTitle, style: Theme.of(context).textTheme.headlineMedium),
        actions: [
          IconButton(
            tooltip: context.l10n.commonRefresh,
            onPressed: () => ref.read(memoryViewModelProvider.notifier).refresh(),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton(
        heroTag: 'add_memory',
        onPressed: () => _showAdd(context, ref),
        child: const Icon(Icons.add),
      ),
      body: Column(
        children: [
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.lg),
            child: Row(
              children: [
                Padding(
                  padding: const EdgeInsetsDirectional.only(end: AuraSpacing.sm),
                  child: FilterChip(
                    label: Text(context.l10n.memoryFilterAll),
                    selected: state.kindFilter == null,
                    onSelected: (_) =>
                        ref.read(memoryViewModelProvider.notifier).setFilter(null),
                  ),
                ),
                for (final kind in kMemoryKinds)
                  Padding(
                    padding: const EdgeInsetsDirectional.only(end: AuraSpacing.sm),
                    child: FilterChip(
                      label: Text(memoryKindLabel(context.l10n, kind)),
                      selected: state.kindFilter == kind,
                      onSelected: (_) =>
                          ref.read(memoryViewModelProvider.notifier).setFilter(kind),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: AuraSpacing.md),
          // Above the list when there is one, so a failed refresh is said
          // even while older memories are still on screen.
          if (!state.loading && state.error != null && state.visible.isNotEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.lg),
              child: AuraFailureView(
                failure: AuraFailure.from(state.error),
                compact: true,
                onRetry: () => ref.read(memoryViewModelProvider.notifier).refresh(),
              ),
            ),
          Expanded(
            child: state.loading
                ? const Center(child: CircularProgressIndicator())
                : state.error != null && state.visible.isEmpty
                    ? AuraFailureView(
                        failure: AuraFailure.from(state.error),
                        onRetry: () => ref.read(memoryViewModelProvider.notifier).refresh(),
                      )
                    : state.visible.isEmpty
                    ? AuraEmptyState(
                        icon: Icons.psychology_outlined,
                        title: context.l10n.memoryEmptyTitle,
                        message: context.l10n.memoryEmptyMessage,
                      )
                    : ListView.separated(
                        padding: const EdgeInsets.fromLTRB(
                          AuraSpacing.lg,
                          0,
                          AuraSpacing.lg,
                          88,
                        ),
                        itemCount: state.visible.length,
                        separatorBuilder: (_, _) =>
                            const SizedBox(height: AuraSpacing.sm),
                        itemBuilder: (context, index) {
                          final item = state.visible[index];
                          return AuraCard(
                            child: ListTile(
                              contentPadding: EdgeInsets.zero,
                              title: Text(item.content),
                              subtitle: Text(
                                [
                                  memoryKindLabel(context.l10n, item.kind),
                                  if (item.subject != null && item.subject!.isNotEmpty)
                                    item.subject,
                                ].join(' · '),
                              ),
                              trailing: IconButton(
                                tooltip: context.l10n.commonDelete,
                                icon: const Icon(Icons.delete_outline),
                                onPressed: () => _remove(context, ref, item.id),
                              ),
                            ),
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }

  Future<void> _remove(BuildContext context, WidgetRef ref, String id) async {
    try {
      final confirmed = await ref.read(memoryViewModelProvider.notifier).remove(id);
      if (!context.mounted) return;
      AuraSnackbar.show(
        context,
        message: confirmed
            ? context.l10n.memoryForgotten
            : context.l10n.memoryForgottenLocally,
        kind: confirmed ? AuraSnackbarKind.success : AuraSnackbarKind.info,
      );
    } catch (e) {
      if (!context.mounted) return;
      final failure = AuraFailure.from(e);
      AuraSnackbar.show(
        context,
        message: context.l10n.memoryForgetFailed(failure.message),
        kind: AuraSnackbarKind.error,
      );
    }
  }

  Future<void> _showAdd(BuildContext context, WidgetRef ref) async {
    final content = TextEditingController();
    final subject = TextEditingController();
    var kind = 'semantic';
    await AuraDialog.show<void>(
      context: context,
      title: context.l10n.memoryAddTitle,
      confirmLabel: context.l10n.commonSave,
      cancelLabel: context.l10n.cancel,
      onConfirm: () async {
        final text = content.text.trim();
        if (text.isEmpty) return;
        await ref.read(memoryViewModelProvider.notifier).add(
              content: text,
              kind: kind,
              subject: subject.text.trim().isEmpty ? null : subject.text.trim(),
            );
        if (context.mounted) Navigator.of(context).pop();
      },
      child: StatefulBuilder(
        builder: (context, setState) {
          return Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              AuraTextField(controller: content, label: context.l10n.memoryFactLabel, maxLines: 3),
              const SizedBox(height: AuraSpacing.md),
              AuraTextField(controller: subject, label: context.l10n.memorySubjectLabel),
              const SizedBox(height: AuraSpacing.md),
              DropdownButtonFormField<String>(
                initialValue: kind,
                items: [
                  for (final value in kMemoryKinds)
                    DropdownMenuItem(value: value, child: Text(memoryKindLabel(context.l10n, value))),
                ],
                onChanged: (value) {
                  if (value != null) setState(() => kind = value);
                },
                decoration: InputDecoration(labelText: context.l10n.memoryKindField),
              ),
            ],
          );
        },
      ),
    );
    content.dispose();
    subject.dispose();
  }
}

/// A memory kind (a backend identifier such as `semantic`) in the language on screen; an
/// unknown kind is shown as sent.
String memoryKindLabel(AppLocalizations l10n, String kind) {
  switch (kind) {
    case 'working':
      return l10n.memoryKindWorking;
    case 'episodic':
      return l10n.memoryKindEpisodic;
    case 'semantic':
      return l10n.memoryKindSemantic;
    case 'entity':
      return l10n.memoryKindEntity;
    case 'procedural':
      return l10n.memoryKindProcedural;
    case 'reflection':
      return l10n.memoryKindReflection;
    default:
      return kind;
  }
}
