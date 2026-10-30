import 'package:flutter/material.dart';
import '../aura_palette.dart';
import '../tokens.dart';
import 'aura_button.dart';
import 'package:aura_client/l10n/l10n.dart';

/// What kind of nothing this is.
///
/// The distinction is not decoration. "There is nothing here yet" and "this
/// failed" and "you are offline" call for different responses from the user,
/// and rendering all three as the same grey block is how a broken screen gets
/// mistaken for an empty one — and an empty one for a broken one.
enum AuraStateTone {
  /// Nothing here yet. Not a problem.
  neutral,

  /// Something failed and may be recoverable.
  error,

  /// Something is degraded but working — stale data, a pending sync.
  warning,

  /// A connection is unavailable. Distinct from [error] because the user's
  /// own work is usually still safe and local, and saying so is the point.
  offline,
}

/// Standard empty / error / offline state — icon, title, explanation, and the
/// way out.
///
/// V1's empty states were a one-line grey `Text` centered in a `SizedBox`,
/// which reads as broken rather than intentional. Every state rendered through
/// this one should answer the three questions an empty screen otherwise leaves
/// hanging: **what is empty, why, and what can I do about it** — so [message]
/// is effectively required in practice, and an error state without an
/// [actionLabel] is a dead end.
class AuraEmptyState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String? message;
  final String? actionLabel;
  final VoidCallback? onAction;

  /// A second way out, for cases where retrying is not the only thing worth
  /// offering — "Retry" *and* "Check connection", say.
  final String? secondaryActionLabel;
  final VoidCallback? onSecondaryAction;

  final AuraStateTone tone;

  const AuraEmptyState({
    super.key,
    required this.icon,
    required this.title,
    this.message,
    this.actionLabel,
    this.onAction,
    this.secondaryActionLabel,
    this.onSecondaryAction,
    this.tone = AuraStateTone.neutral,
  });

  Color _iconColor(AuraPalette palette) => switch (tone) {
        AuraStateTone.neutral => palette.textDisabled,
        AuraStateTone.error => palette.danger,
        AuraStateTone.warning => palette.warning,
        AuraStateTone.offline => palette.textSecondary,
      };

  /// Prefixed to the announced label so a screen reader hears the kind of
  /// state, not just its title — the icon's colour carries that for everyone
  /// else, and colour alone is not a signal.
  String? _tonePrefix(BuildContext context) => switch (tone) {
        AuraStateTone.neutral => null,
        AuraStateTone.error => context.l10n.stateToneError,
        AuraStateTone.warning => context.l10n.stateToneWarning,
        AuraStateTone.offline => context.l10n.stateToneOffline,
      };

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final prefix = _tonePrefix(context);

    return Semantics(
      // A state view replaces content that was there a moment ago, so its
      // arrival is worth announcing rather than leaving the user to discover
      // by exploring.
      liveRegion: true,
      label: <String>[
        ?prefix,
        title,
        ?message,
      ].join('. '),
      // The label above already says everything; without this a screen reader
      // reads the same words twice.
      excludeSemantics: true,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: AuraSpacing.xxl, horizontal: AuraSpacing.lg),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 36, color: _iconColor(palette)),
            const SizedBox(height: AuraSpacing.md),
            Text(title, style: Theme.of(context).textTheme.titleSmall, textAlign: TextAlign.center),
            if (message != null) ...[
              const SizedBox(height: AuraSpacing.xs),
              Text(
                message!,
                style: Theme.of(context).textTheme.bodySmall,
                textAlign: TextAlign.center,
              ),
            ],
            if (_hasAction || _hasSecondaryAction) ...[
              const SizedBox(height: AuraSpacing.lg),
              Wrap(
                spacing: AuraSpacing.sm,
                runSpacing: AuraSpacing.sm,
                alignment: WrapAlignment.center,
                children: [
                  if (_hasAction)
                    AuraButton(
                      label: actionLabel!,
                      onPressed: onAction,
                      variant: AuraButtonVariant.secondary,
                    ),
                  if (_hasSecondaryAction)
                    AuraButton(
                      label: secondaryActionLabel!,
                      onPressed: onSecondaryAction,
                      variant: AuraButtonVariant.ghost,
                    ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  bool get _hasAction => actionLabel != null && onAction != null;
  bool get _hasSecondaryAction => secondaryActionLabel != null && onSecondaryAction != null;
}
