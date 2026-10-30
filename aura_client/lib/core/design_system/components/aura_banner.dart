import 'package:flutter/material.dart';

import '../aura_palette.dart';
import '../tokens.dart';
import 'aura_empty_state.dart' show AuraStateTone;

/// A full-width strip that says something is wrong with the *app*, not with
/// the screen you are on.
///
/// Distinct from [AuraSnackbar], which is transient and easy to miss, and from
/// `AuraFailureView`, which replaces the content that failed. A banner is for
/// a condition that persists and travels with the user: it belongs in shared
/// chrome, stays until the condition clears, and must not be the only place a
/// problem is reported if the problem is specific to one screen.
///
/// Deliberately not dismissible by default. A banner whose condition is still
/// true after dismissal is a problem the user has been invited to forget.
class AuraBanner extends StatelessWidget {
  const AuraBanner({
    super.key,
    required this.icon,
    required this.message,
    this.tone = AuraStateTone.warning,
    this.actionLabel,
    this.onAction,
    this.semanticLabel,
  });

  final IconData icon;
  final String message;
  final AuraStateTone tone;
  final String? actionLabel;
  final VoidCallback? onAction;

  /// Overrides what a screen reader hears, for when [message] is written
  /// tight enough to need context spelled out.
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final accent = switch (tone) {
      AuraStateTone.neutral => palette.textSecondary,
      AuraStateTone.error => palette.danger,
      AuraStateTone.warning => palette.warning,
      AuraStateTone.offline => palette.textSecondary,
    };
    final showAction = actionLabel != null && onAction != null;

    return Semantics(
      liveRegion: true,
      label: semanticLabel ?? message,
      excludeSemantics: true,
      child: Material(
        color: accent.withValues(alpha: 0.12),
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AuraSpacing.lg,
            vertical: AuraSpacing.sm,
          ),
          child: Row(
            children: [
              Icon(icon, size: 18, color: accent),
              const SizedBox(width: AuraSpacing.sm),
              Expanded(
                child: Text(
                  message,
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        color: palette.textPrimary,
                      ),
                ),
              ),
              if (showAction) ...[
                const SizedBox(width: AuraSpacing.sm),
                TextButton(
                  onPressed: onAction,
                  style: TextButton.styleFrom(
                    visualDensity: VisualDensity.compact,
                    foregroundColor: accent,
                  ),
                  child: Text(actionLabel!),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
