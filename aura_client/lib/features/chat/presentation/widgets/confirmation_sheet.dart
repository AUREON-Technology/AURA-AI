import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/presentation/chat_view_model.dart';

class ConfirmationCard extends ConsumerWidget {
  const ConfirmationCard({
    super.key,
    this.onConfirm,
    this.onCancel,
  });

  /// When set, used instead of [chatViewModelProvider.confirmTools].
  final VoidCallback? onConfirm;

  /// When set, used instead of clearing [pendingConfirmationProvider] alone.
  final VoidCallback? onCancel;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tools = ref.watch(pendingConfirmationProvider);
    if (tools.isEmpty) return const SizedBox.shrink();

    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AuraSpacing.xl,
        vertical: AuraSpacing.sm,
      ),
      child: AuraCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              'Confirm tools',
              style: Theme.of(context).textTheme.titleSmall,
            ),
            const SizedBox(height: AuraSpacing.sm),
            Text(
              'This turn used untrusted context. Re-run the last question with these tools allowed:',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: AuraSpacing.sm),
            Text(tools.join(', '), style: Theme.of(context).textTheme.bodyMedium),
            const SizedBox(height: AuraSpacing.md),
            Row(
              children: [
                Expanded(
                  child: AuraButton(
                    label: 'Cancel',
                    variant: AuraButtonVariant.secondary,
                    onPressed: onCancel ??
                        () => ref.read(pendingConfirmationProvider.notifier).clear(),
                  ),
                ),
                const SizedBox(width: AuraSpacing.md),
                Expanded(
                  child: AuraButton(
                    label: 'Confirm',
                    onPressed: onConfirm ??
                        () => ref.read(chatViewModelProvider.notifier).confirmTools(),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
