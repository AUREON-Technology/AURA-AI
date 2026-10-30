import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';

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
        title: Text('Memory', style: Theme.of(context).textTheme.headlineMedium),
        actions: [
          IconButton(
            tooltip: 'Refresh',
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
                  padding: const EdgeInsets.only(right: AuraSpacing.sm),
                  child: FilterChip(
                    label: const Text('All'),
                    selected: state.kindFilter == null,
                    onSelected: (_) =>
                        ref.read(memoryViewModelProvider.notifier).setFilter(null),
                  ),
                ),
                for (final kind in kMemoryKinds)
                  Padding(
                    padding: const EdgeInsets.only(right: AuraSpacing.sm),
                    child: FilterChip(
                      label: Text(kind),
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
                    ? const AuraEmptyState(
                        icon: Icons.psychology_outlined,
                        title: 'No memories yet',
                        message: 'Save a fact with a kind so Copilot can use it later.',
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
                                  item.kind,
                                  if (item.subject != null && item.subject!.isNotEmpty)
                                    item.subject,
                                ].join(' · '),
                              ),
                              trailing: IconButton(
                                tooltip: 'Delete',
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
            ? 'Memory forgotten'
            : 'Forgotten on this device. AURA will finish removing it from the '
                'server the next time it can reach it.',
        kind: confirmed ? AuraSnackbarKind.success : AuraSnackbarKind.info,
      );
    } catch (e) {
      if (!context.mounted) return;
      final failure = AuraFailure.from(e);
      AuraSnackbar.show(
        context,
        message: 'Could not forget this memory. ${failure.message}',
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
      title: 'Remember this',
      confirmLabel: 'Save',
      cancelLabel: 'Cancel',
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
              AuraTextField(controller: content, label: 'Fact', maxLines: 3),
              const SizedBox(height: AuraSpacing.md),
              AuraTextField(controller: subject, label: 'Subject (optional)'),
              const SizedBox(height: AuraSpacing.md),
              DropdownButtonFormField<String>(
                initialValue: kind,
                items: [
                  for (final value in kMemoryKinds)
                    DropdownMenuItem(value: value, child: Text(value)),
                ],
                onChanged: (value) {
                  if (value != null) setState(() => kind = value);
                },
                decoration: const InputDecoration(labelText: 'Kind'),
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
