import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/l10n/l10n.dart';

class TurnStatusRow extends StatelessWidget {
  final TurnMeta meta;
  final bool hasCitations;

  const TurnStatusRow({
    super.key,
    required this.meta,
    required this.hasCitations,
  });

  // The English copies below are what tests look up; the row itself renders the
  // localized strings (chatTurn* keys), whose English text is identical.
  static const libraryEmptyCopy =
      'Searched your library — nothing relevant was found.';

  /// ADR-006: retrieved knowledge and tool output travel on the DATA trust
  /// channel, and a turn whose context was quarantined cannot run a
  /// `side_effecting` tool without explicit confirmation. That is the
  /// mechanism that stops a poisoned document from silently triggering a
  /// write — and until now the user was never told it had engaged.
  ///
  /// Worded as a protection rather than an accusation: the content being
  /// untrusted is the normal state for anything retrieved, and the thing
  /// worth knowing is that nothing can act on it unasked.
  static const quarantinedContextCopy =
      'AURA treated the content it read as untrusted, so nothing with side '
      'effects can run without your confirmation.';
  static const reducedModeCopy = 'AURA ran this turn in a reduced mode.';
  static const ungroundedCopy = 'Ungrounded — no sources attached.';
  static const interruptedCopy = 'Interrupted — this answer is incomplete.';

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // First, because it is the only note here that bears on what the
        // answer is allowed to *do* rather than on how good it is.
        if (meta.tainted)
          _Note(
            icon: Icons.gpp_maybe_outlined,
            color: palette.warning,
            text: l10n.chatTurnQuarantined,
          ),
        if (meta.interrupted)
          _Note(
            icon: Icons.stop_circle_outlined,
            color: palette.warning,
            text: l10n.chatTurnInterrupted,
          ),
        if (meta.revised)
          _Note(
            icon: Icons.shield_outlined,
            color: palette.warning,
            text: l10n.chatTurnRedacted,
          ),
        if (meta.showLowConfidence)
          _Note(
            icon: Icons.speed_outlined,
            color: palette.warning,
            text: l10n.chatTurnLowConfidence((meta.confidence! * 100).round()),
          ),
        if (meta.degraded && meta.searchedButEmpty)
          _Note(
            icon: Icons.link_off,
            color: palette.textSecondary,
            text: l10n.chatTurnLibraryEmpty,
          )
        else if (meta.degraded)
          _Note(
            icon: Icons.cloud_queue,
            color: palette.textSecondary,
            text: l10n.chatTurnReducedMode,
          )
        else if (!hasCitations)
          _Note(
            icon: Icons.link_off,
            color: palette.textSecondary,
            text: meta.searchedButEmpty ? l10n.chatTurnLibraryEmpty : l10n.chatTurnUngrounded,
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
