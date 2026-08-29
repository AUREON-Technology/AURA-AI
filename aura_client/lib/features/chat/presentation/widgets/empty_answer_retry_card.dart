import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';

/// Shown when an assistant turn ends with no tokens instead of a blank body.
class EmptyAnswerRetryCard extends StatelessWidget {
  final VoidCallback onRetry;

  const EmptyAnswerRetryCard({super.key, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(Icons.refresh, color: palette.warning, size: 20),
            const SizedBox(width: AuraSpacing.sm),
            Expanded(
              child: Text(
                'No reply this turn',
                style: TextStyle(
                  fontWeight: FontWeight.bold,
                  color: palette.textPrimary,
                  fontSize: 14,
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: AuraSpacing.sm),
        Text(
          'AURA finished without an answer. Retry to send your last message again.',
          style: TextStyle(
            color: palette.textSecondary,
            fontSize: 13,
            height: 1.4,
          ),
        ),
        const SizedBox(height: AuraSpacing.md),
        AuraButton(
          label: 'Retry',
          icon: Icons.refresh,
          onPressed: onRetry,
        ),
      ],
    );
  }
}
