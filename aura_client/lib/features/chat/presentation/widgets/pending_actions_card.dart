import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import '../../data/chat_repository.dart';
import '../chat_view_model.dart';
import '../pending_actions_executor.dart';
import 'package:aura_client/l10n/l10n.dart';

class PendingActionsCard extends ConsumerWidget {
  const PendingActionsCard({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final actions = ref.watch(pendingActionsProvider);
    if (actions.isEmpty) return const SizedBox();

    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AuraSpacing.xl,
        vertical: AuraSpacing.sm,
      ),
      child: AuraCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Icon(Icons.auto_awesome, color: palette.success, size: 20),
                const SizedBox(width: AuraSpacing.sm),
                Expanded(
                  child: Text(
                    context.l10n.pendingActionsTitle,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontWeight: FontWeight.bold,
                      fontSize: 14,
                      color: palette.textPrimary,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: AuraSpacing.md),
            ...actions.map((act) {
              final type = act['type'] ?? '';
              final params = act['params'] ?? {};
              final display = _displayFor(type, params, palette);
              return Padding(
                padding: const EdgeInsets.only(bottom: AuraSpacing.sm),
                child: Row(
                  children: [
                    Icon(display.icon, size: 16, color: display.color),
                    const SizedBox(width: AuraSpacing.sm),
                    Expanded(
                      child: Text(
                        display.label,
                        style: TextStyle(
                          fontSize: 13,
                          color: palette.textSecondary,
                        ),
                      ),
                    ),
                  ],
                ),
              );
            }),
            Divider(color: palette.border, height: 20),
            Wrap(
              alignment: WrapAlignment.end,
              spacing: AuraSpacing.sm,
              runSpacing: AuraSpacing.sm,
              children: [
                AuraButton(
                  label: context.l10n.pendingActionsReject,
                  variant: AuraButtonVariant.ghost,
                  onPressed: () => ref
                      .read(chatViewModelProvider.notifier)
                      .rejectPendingActions(),
                ),
                AuraButton(
                  label: context.l10n.pendingActionsApprove,
                  variant: AuraButtonVariant.primary,
                  onPressed: () async {
                    final messenger = ScaffoldMessenger.of(context);
                    final outcome = await ref
                        .read(chatViewModelProvider.notifier)
                        .approvePendingActions();
                    if (!context.mounted) return;
                    messenger.showSnackBar(_snackBarFor(outcome, palette, context.l10n));
                  },
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  SnackBar _snackBarFor(PendingActionsOutcome outcome, AuraPalette palette, AppLocalizations l10n) {
    if (outcome.allSucceeded) {
      return SnackBar(
        content: Text(l10n.pendingActionsAllSucceeded),
        backgroundColor: palette.success,
      );
    }
    if (outcome.allFailed) {
      return SnackBar(
        content: Text(l10n.pendingActionsAllFailed(outcome.failedLabels.join('; '))),
        backgroundColor: palette.danger,
      );
    }
    return SnackBar(
      content: Text(
        l10n.pendingActionsSomeFailed(outcome.failedLabels.length, outcome.failedLabels.join('; ')),
      ),
      backgroundColor: palette.warning,
    );
  }
}

class _ActionDisplay {
  final String label;
  final IconData icon;
  final Color color;
  const _ActionDisplay({
    required this.label,
    required this.icon,
    required this.color,
  });
}

_ActionDisplay _displayFor(dynamic type, dynamic params, AuraPalette palette) {
  final t = type?.toString() ?? '';
  final p = params is Map ? params : const {};
  if (t == 'add_task') {
    return _ActionDisplay(
      label:
          'Create Task: ${p['title']} (${p['priority']} priority, ${p['category']})',
      icon: Icons.playlist_add,
      color: palette.success,
    );
  }
  if (t == 'add_event') {
    return _ActionDisplay(
      label: 'Schedule Event: ${p['title']} at ${p['startTime']}',
      icon: Icons.event,
      color: palette.brand,
    );
  }
  if (t == 'add_expense') {
    return _ActionDisplay(
      label: 'Log Expense: \$${p['amount']} for ${p['category']}',
      icon: Icons.remove_circle_outline,
      color: palette.danger,
    );
  }
  if (t == 'add_income') {
    return _ActionDisplay(
      label: 'Log Income: \$${p['amount']} for ${p['category']}',
      icon: Icons.add_circle_outline,
      color: palette.success,
    );
  }
  if (t == 'log_health') {
    final metrics = <String>[];
    if (p['waterIntakeMl'] != null) {
      metrics.add('${p['waterIntakeMl']}ml water');
    }
    if (p['sleepMinutes'] != null) {
      metrics.add('${p['sleepMinutes']}m sleep');
    }
    if (p['caloriesConsumed'] != null) {
      metrics.add('${p['caloriesConsumed']}kcal');
    }
    if (p['exerciseMinutes'] != null) {
      metrics.add('${p['exerciseMinutes']}m exercise');
    }
    if (p['weight'] != null) {
      metrics.add('${p['weight']}kg');
    }
    return _ActionDisplay(
      label: 'Log Health: ${metrics.join(', ')}',
      icon: Icons.favorite_outline,
      color: Colors.pinkAccent,
    );
  }
  return _ActionDisplay(
    label: pendingActionLabel(t, p),
    icon: Icons.check_circle_outline,
    color: palette.brand,
  );
}
