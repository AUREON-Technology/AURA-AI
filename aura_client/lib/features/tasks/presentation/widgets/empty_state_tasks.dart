import 'package:flutter/material.dart';

import 'package:aura_client/core/theme/theme.dart';

class EmptyStateTasks extends StatelessWidget {
  const EmptyStateTasks({super.key});

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Icon(
          Icons.task_alt,
          size: 72,
          color: AuraTheme.textSecondary.withValues(alpha: 0.3),
        ),
        const SizedBox(height: 16),
        const Text(
          'All caught up!',
          style: TextStyle(
            color: AuraTheme.textPrimary,
            fontSize: 18,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 4),
        const Text(
          'Enjoy your day or create a new task to stay organized.',
          style: TextStyle(
            color: AuraTheme.textSecondary,
            fontSize: 14,
          ),
          textAlign: TextAlign.center,
        ),
      ],
    );
  }
}
