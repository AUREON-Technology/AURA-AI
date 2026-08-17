import 'package:flutter/material.dart';
import '../../../../core/design_system/design_system.dart';

/// Shown in place of an assistant message's body while its turn is still
/// streaming and no token text has arrived yet. Replaces the old literal
/// "..." placeholder text, which could not distinguish "still streaming"
/// from "the model legitimately returned an empty answer" -- this is gated
/// by `streamingMessageIdProvider` (chat_repository.dart), not by
/// content-is-empty alone.
class StreamingMessageSkeleton extends StatelessWidget {
  const StreamingMessageSkeleton({super.key});

  @override
  Widget build(BuildContext context) {
    return const Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        AuraSkeleton(height: 12, width: 220),
        SizedBox(height: AuraSpacing.sm),
        AuraSkeleton(height: 12, width: 160),
      ],
    );
  }
}
