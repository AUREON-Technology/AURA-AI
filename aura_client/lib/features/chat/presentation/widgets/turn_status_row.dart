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

  static const libraryEmptyCopy =
      'Searched your library — nothing relevant was found.';
  static const reducedModeCopy = 'AURA ran this turn in a reduced mode.';
  static const ungroundedCopy = 'Ungrounded — no sources attached.';

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
        if (meta.degraded && meta.searchedButEmpty)
          _Note(
            icon: Icons.link_off,
            color: palette.textSecondary,
            text: libraryEmptyCopy,
          )
        else if (meta.degraded)
          _Note(
            icon: Icons.cloud_queue,
            color: palette.textSecondary,
            text: reducedModeCopy,
          )
        else if (!hasCitations)
          _Note(
            icon: Icons.link_off,
            color: palette.textSecondary,
            text: meta.searchedButEmpty ? libraryEmptyCopy : ungroundedCopy,
          ),
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
