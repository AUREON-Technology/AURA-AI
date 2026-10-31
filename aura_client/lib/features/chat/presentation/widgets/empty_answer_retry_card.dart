import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

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
                context.l10n.chatNoReplyTitle,
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
          context.l10n.chatNoReplyBody,
          style: TextStyle(
            color: palette.textSecondary,
            fontSize: 13,
            height: 1.4,
          ),
        ),
        const SizedBox(height: AuraSpacing.md),
        AuraButton(
          label: context.l10n.retry,
          icon: Icons.refresh,
          onPressed: onRetry,
        ),
      ],
    );
  }
}
