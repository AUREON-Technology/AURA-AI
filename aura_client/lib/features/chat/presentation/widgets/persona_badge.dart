import 'package:flutter/material.dart';
import '../../../../core/design_system/design_system.dart';

/// Small identity badge shown at the top of an assistant message, naming
/// which persona (aura_core/prompts/library.py's AGENT_PROMPT_IDS) answered
/// this specific turn. Purely presentational -- the caller resolves
/// [label]/[icon] from the message's stored `agentId` via
/// `features/chat/data/agents_repository.dart`'s lookup, so this widget has
/// no opinion on where persona metadata comes from.
class PersonaBadge extends StatelessWidget {
  final String label;
  final IconData icon;

  const PersonaBadge({super.key, required this.label, required this.icon});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 14, color: palette.brandOnSurfaceText),
        const SizedBox(width: 4),
        Text(
          label,
          style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: palette.brandOnSurfaceText),
        ),
      ],
    );
  }
}
