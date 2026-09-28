/// Renders an [AuraFailure] as a recoverable state.
///
/// Lives here rather than in `core/design_system/` so the UI kit stays
/// ignorant of the network layer: [AuraEmptyState] knows how to *look* like a
/// failure, this knows which failure it is.
library;

import 'package:flutter/material.dart';

import '../design_system/components/aura_empty_state.dart';
import 'aura_failure.dart';

class AuraFailureView extends StatelessWidget {
  const AuraFailureView({
    super.key,
    required this.failure,
    this.onRetry,
    this.secondaryActionLabel,
    this.onSecondaryAction,
    this.showDetail = true,
  });

  final AuraFailure failure;

  /// Wired to a "Retry" button, but **only** when the failure is one that
  /// retrying can actually fix. Offering it on a safety refusal or a
  /// permission error invites the user to bang on a door that will not open.
  final VoidCallback? onRetry;

  final String? secondaryActionLabel;
  final VoidCallback? onSecondaryAction;

  /// Appends the server's own message under the explanation. Useful in
  /// developer-facing surfaces, noise in a consumer one.
  final bool showDetail;

  static IconData iconFor(AuraFailureKind kind) => switch (kind) {
        AuraFailureKind.unreachable => Icons.cloud_off,
        AuraFailureKind.inferenceUnavailable => Icons.memory_outlined,
        AuraFailureKind.busy => Icons.hourglass_top_outlined,
        AuraFailureKind.rateLimited => Icons.speed_outlined,
        AuraFailureKind.turnInFlight => Icons.pending_outlined,
        AuraFailureKind.authRequired => Icons.lock_outline,
        AuraFailureKind.forbidden => Icons.block_outlined,
        AuraFailureKind.budgetExceeded => Icons.account_balance_wallet_outlined,
        AuraFailureKind.blocked => Icons.gpp_maybe_outlined,
        AuraFailureKind.indexing => Icons.hourglass_empty_outlined,
        AuraFailureKind.degraded => Icons.warning_amber_outlined,
        AuraFailureKind.unknown => Icons.error_outline,
      };

  /// Tone is about what the user should *do*, not about how bad it sounds.
  /// A rate limit and a still-building index are both "wait", not "broken";
  /// an expired session is neither — it is a thing to go and fix.
  static AuraStateTone toneFor(AuraFailureKind kind) => switch (kind) {
        AuraFailureKind.unreachable => AuraStateTone.offline,
        AuraFailureKind.busy ||
        AuraFailureKind.rateLimited ||
        AuraFailureKind.turnInFlight ||
        AuraFailureKind.indexing ||
        AuraFailureKind.degraded ||
        AuraFailureKind.inferenceUnavailable =>
          AuraStateTone.warning,
        AuraFailureKind.authRequired ||
        AuraFailureKind.forbidden ||
        AuraFailureKind.budgetExceeded ||
        AuraFailureKind.blocked ||
        AuraFailureKind.unknown =>
          AuraStateTone.error,
      };

  @override
  Widget build(BuildContext context) {
    final showRetry = failure.canRetry && onRetry != null;
    final detail = showDetail ? failure.detail : null;

    return AuraEmptyState(
      icon: iconFor(failure.kind),
      tone: toneFor(failure.kind),
      title: failure.title,
      message: detail == null ? failure.message : '${failure.message}\n\n$detail',
      actionLabel: showRetry ? 'Retry' : null,
      onAction: showRetry ? onRetry : null,
      secondaryActionLabel: onSecondaryAction == null ? null : secondaryActionLabel,
      onSecondaryAction: onSecondaryAction,
    );
  }
}
