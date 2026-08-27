import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/voice_live/presentation/live_voice_state.dart';

class LiveTranscriptPanel extends StatelessWidget {
  const LiveTranscriptPanel({super.key, required this.lines, this.draft = ''});

  final List<LiveTranscriptLine> lines;
  final String draft;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final items = [
      ...lines,
      if (draft.trim().isNotEmpty)
        LiveTranscriptLine(role: 'assistant', text: draft),
    ];
    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.lg),
      itemCount: items.length,
      itemBuilder: (context, index) {
        final line = items[index];
        final isUser = line.role == 'user';
        return Align(
          alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
          child: Container(
            margin: const EdgeInsets.only(bottom: AuraSpacing.sm),
            padding: const EdgeInsets.symmetric(
              horizontal: AuraSpacing.md,
              vertical: AuraSpacing.sm,
            ),
            decoration: BoxDecoration(
              color: isUser ? palette.brand.withValues(alpha: 0.12) : palette.surfaceVariant,
              borderRadius: BorderRadius.circular(AuraRadius.md),
            ),
            child: Text(
              line.text,
              style: TextStyle(color: palette.textPrimary, fontSize: 14),
            ),
          ),
        );
      },
    );
  }
}
