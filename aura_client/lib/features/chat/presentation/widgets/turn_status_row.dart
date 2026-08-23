import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';

class TurnStatusRow extends StatelessWidget {
  final TurnMeta meta;
  final bool hasCitations;

  const TurnStatusRow({
    super.key,
    required this.meta,
    required this.hasCitations,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (meta.revised)
          _Note(
            icon: Icons.shield_outlined,
            color: palette.warning,
            text: 'This answer was redacted by a safety check.',
          ),
        if (meta.showLowConfidence)
          _Note(
            icon: Icons.speed_outlined,
            color: palette.warning,
            text: 'Low confidence (${(meta.confidence! * 100).round()}%).',
          ),
        if (meta.degraded)
          _Note(
            icon: Icons.cloud_queue,
            color: palette.textSecondary,
            text: 'AURA ran this turn in a reduced mode.',
          ),
        if (!hasCitations)
          _Note(
            icon: Icons.link_off,
            color: palette.textSecondary,
            text: meta.searchedButEmpty
                ? 'Searched your library — nothing relevant was found.'
                : 'Ungrounded — no sources attached.',
          ),
        if (meta.steps.isNotEmpty) ...[
          const SizedBox(height: AuraSpacing.sm),
          for (final step in meta.steps)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Text(
                '• ${step.label ?? step.kind ?? 'step'} (${step.status ?? 'done'})',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
        ],
      ],
    );
  }
}

class _Note extends StatelessWidget {
  final IconData icon;
  final Color color;
  final String text;
  const _Note({required this.icon, required this.color, required this.text});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: AuraSpacing.sm),
      child: Row(
        children: [
          Icon(icon, size: 14, color: color),
          const SizedBox(width: 6),
          Expanded(
            child: Text(text, style: Theme.of(context).textTheme.bodySmall),
          ),
        ],
      ),
    );
  }
}
