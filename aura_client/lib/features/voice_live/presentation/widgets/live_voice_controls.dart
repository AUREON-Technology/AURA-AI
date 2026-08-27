import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';

class LiveVoiceControls extends StatelessWidget {
  const LiveVoiceControls({
    super.key,
    required this.phase,
    required this.transcriptVisible,
    required this.onToggleTranscript,
    required this.onEnd,
    required this.onBargeIn,
    this.onConfirm,
    this.onReject,
    this.hasPendingConfirmation = false,
  });

  final String phase;
  final bool transcriptVisible;
  final VoidCallback onToggleTranscript;
  final VoidCallback onEnd;
  final VoidCallback onBargeIn;
  final VoidCallback? onConfirm;
  final VoidCallback? onReject;
  final bool hasPendingConfirmation;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (hasPendingConfirmation) ...[
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              AuraButton(
                label: 'Confirm',
                onPressed: onConfirm,
              ),
              const SizedBox(width: AuraSpacing.md),
              AuraButton(
                label: 'Reject',
                variant: AuraButtonVariant.secondary,
                onPressed: onReject,
              ),
            ],
          ),
          const SizedBox(height: AuraSpacing.md),
        ],
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            IconButton(
              tooltip: 'Transcript',
              onPressed: onToggleTranscript,
              icon: Icon(
                transcriptVisible ? Icons.subtitles : Icons.subtitles_off,
                color: palette.textPrimary,
              ),
            ),
            const SizedBox(width: AuraSpacing.md),
            IconButton(
              tooltip: 'Interrupt',
              onPressed: phase == 'speaking' || phase == 'thinking' ? onBargeIn : null,
              icon: Icon(Icons.hearing_disabled, color: palette.warning),
            ),
            const SizedBox(width: AuraSpacing.md),
            IconButton(
              tooltip: 'End',
              onPressed: onEnd,
              icon: Icon(Icons.call_end, color: palette.danger),
            ),
          ],
        ),
      ],
    );
  }
}
