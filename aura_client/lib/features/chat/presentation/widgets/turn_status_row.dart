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
  static const noActionTakenCopy =
      'No action was taken — nothing was created, changed or sent.';
  static const toolsUnavailableCopy =
      "AURA couldn't use its tools for this turn, so nothing was done.";
  static const toolFailedCopy = 'A tool AURA used failed during this turn.';
  static const emptyReplyCopy = 'The model returned no reply for this turn.';
  static const toolOutputTrimmedCopy =
      "A tool's result was too long to use in full.";

  /// The one note a degraded turn's reasons call for (audit C3), most consequential first:
  /// an answer that claimed an action, then tools that could not run, then a failed tool.
  /// `null` leaves the generic notes below in charge (fallback, taint, empty library).
  static String? degradedNote(AppLocalizations l10n, List<String> reasons) {
    if (reasons.contains('unbacked_action_claim')) return l10n.chatTurnNoActionTaken;
    if (reasons.contains('planning_failed')) return l10n.chatTurnToolsUnavailable;
    if (reasons.contains('tool_failure')) return l10n.chatTurnToolFailed;
    if (reasons.contains('empty_generation_fallback')) return l10n.chatTurnEmptyReply;
    if (reasons.contains('tool_output_dropped')) return l10n.chatTurnToolOutputTrimmed;
    if (reasons.contains('knowledge_unavailable')) return l10n.chatTurnKnowledgeUnavailable;
    return null;
  }
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
        if (degradedNote(l10n, meta.degradedReasons) case final note?)
          _Note(
            icon: Icons.info_outline,
            color: palette.warning,
            text: note,
          )
        else if (meta.degraded && meta.searchedButEmpty)
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
