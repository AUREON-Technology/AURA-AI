import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';

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
          'All caught up!',
          style: TextStyle(
            color: palette.textPrimary,
            fontSize: 18,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          'Enjoy your day or create a new task to stay organized.',
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
