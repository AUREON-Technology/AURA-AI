import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import '../../data/memory_repository.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';

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
                padding: EdgeInsets.only(
                  left: AuraSpacing.xl,
                  right: AuraSpacing.xl,
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
                            'User Memory Bank',
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
                          icon: Icon(Icons.close, color: palette.textDisabled),
                          onPressed: () => Navigator.pop(context),
                        ),
                      ],
                    ),
                    const SizedBox(height: AuraSpacing.xs),
                    Text(
                      'These are facts and preferences AURA has learned about you. They are injected as system prompts to personalize reasoning.',
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
                            decoration: const InputDecoration(
                              hintText: 'Add a custom fact manually...',
                            ),
                            style: TextStyle(
                              color: palette.textPrimary,
                              fontSize: 13,
                            ),
                          ),
                        ),
                        const SizedBox(width: AuraSpacing.sm),
                        AuraButton(
                          label: 'Add',
                          onPressed: () async {
                            final fact = factController.text.trim();
                            if (fact.isNotEmpty) {
                              await ref
                                  .read(memoryRepositoryProvider)
                                  .saveMemory(fact);
                              factController.clear();
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
                                'No memories stored yet. AURA extracts them automatically from conversations, or you can add one above.',
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
