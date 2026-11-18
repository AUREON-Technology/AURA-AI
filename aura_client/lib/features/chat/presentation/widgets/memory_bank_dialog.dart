import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import '../../data/memory_repository.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';
import 'package:aura_client/features/memory/presentation/memory_save_feedback.dart';
import 'package:aura_client/l10n/l10n.dart';

void showMemoryBankDialog(BuildContext context) {
  final factController = TextEditingController();

  showDialog(
    context: context,
    builder: (dialogContext) {
      return Consumer(
        builder: (context, ref, child) {
          final palette = AuraPalette.of(context);
          final memoriesAsync = ref.watch(localMemoriesStreamProvider((limit: 100, offset: 0)));

          return Dialog(
            backgroundColor: palette.surface,
            insetPadding: const EdgeInsets.symmetric(
              horizontal: 16,
              vertical: 24,
            ),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(AuraRadius.lg),
              side: BorderSide(color: palette.border),
            ),
            child: ConstrainedBox(
              constraints: BoxConstraints(
                maxWidth: 500,
                maxHeight: MediaQuery.sizeOf(context).height * 0.75,
              ),
              child: Padding(
                padding: EdgeInsetsDirectional.only(
                  start: AuraSpacing.xl,
                  end: AuraSpacing.xl,
                  top: AuraSpacing.xl,
                  bottom:
                      AuraSpacing.xl + MediaQuery.viewInsetsOf(context).bottom,
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.psychology, color: palette.warning),
                        const SizedBox(width: AuraSpacing.sm),
                        Expanded(
                          child: Text(
                            context.l10n.memoryBankTitle,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              fontSize: 18,
                              fontWeight: FontWeight.bold,
                              color: palette.textPrimary,
                            ),
                          ),
                        ),
                        IconButton(
                          tooltip: context.l10n.close,
                          icon: Icon(Icons.close, color: palette.textDisabled),
                          onPressed: () => Navigator.pop(context),
                        ),
                      ],
                    ),
                    const SizedBox(height: AuraSpacing.xs),
                    Text(
                      context.l10n.memoryBankExplainer,
                      style: TextStyle(
                        color: palette.textSecondary,
                        fontSize: 12,
                      ),
                    ),
                    Divider(color: palette.border, height: 24),
                    Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: factController,
                            decoration: InputDecoration(
                              hintText: context.l10n.memoryBankAddHint,
                            ),
                            style: TextStyle(
                              color: palette.textPrimary,
                              fontSize: 13,
                            ),
                          ),
                        ),
                        const SizedBox(width: AuraSpacing.sm),
                        AuraButton(
                          label: context.l10n.add,
                          onPressed: () async {
                            final fact = factController.text.trim();
                            if (fact.isNotEmpty) {
                              final outcome = await ref
                                  .read(memoryRepositoryProvider)
                                  .saveMemory(fact);
                              factController.clear();
                              if (context.mounted) {
                                showMemorySaveOutcome(context, outcome, fact);
                              }
                            }
                          },
                        ),
                      ],
                    ),
                    const SizedBox(height: AuraSpacing.lg),
                    Expanded(
                      child: memoriesAsync.when(
                        data: (memories) {
                          if (memories.isEmpty) {
                            return Center(
                              child: Text(
                                context.l10n.memoryBankEmpty,
                                textAlign: TextAlign.center,
                                style: TextStyle(
                                  color: palette.textSecondary,
                                  fontSize: 12,
                                ),
                              ),
                            );
                          }
                          return ListView.builder(
                            itemCount: memories.length,
                            itemBuilder: (context, index) {
                              final mem = memories[index];
                              return Padding(
                                padding: const EdgeInsets.only(
                                  bottom: AuraSpacing.sm,
                                ),
                                child: AuraCard(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: AuraSpacing.md,
                                    vertical: AuraSpacing.sm,
                                  ),
                                  child: Row(
                                    children: [
                                      Expanded(
                                        child: Text(
                                          mem.fact,
                                          style: TextStyle(
                                            fontSize: 13,
                                            color: palette.textPrimary,
                                          ),
                                        ),
                                      ),
                                      IconButton(
                                        tooltip: context.l10n.memoryBankDelete,
                                        icon: Icon(
                                          Icons.delete,
                                          size: 16,
                                          color: palette.danger,
                                        ),
                                        onPressed: () => ref
                                            .read(memoryRepositoryProvider)
                                            .deleteMemory(mem.id),
                                      ),
                                    ],
                                  ),
                                ),
                              );
                            },
                          );
                        },
                        loading: () =>
                            const Center(child: CircularProgressIndicator()),
                        error: (e, _) => Center(
                          child: SingleChildScrollView(
                            child: AuraFailureView(failure: AuraFailure.from(e)),
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      );
    },
  ).whenComplete(factController.dispose);
}
