import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

class EmptyStateTasks extends StatelessWidget {
  const EmptyStateTasks({super.key});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Icon(
          Icons.task_alt,
          size: 72,
          color: palette.textSecondary.withValues(alpha: 0.3),
        ),
        const SizedBox(height: 16),
        Text(
          context.l10n.tasksEmptyTitle,
          style: TextStyle(
            color: palette.textPrimary,
            fontSize: 18,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          context.l10n.tasksEmptyMessage,
          style: TextStyle(
            color: palette.textSecondary,
            fontSize: 14,
          ),
          textAlign: TextAlign.center,
        ),
      ],
    );
  }
}
