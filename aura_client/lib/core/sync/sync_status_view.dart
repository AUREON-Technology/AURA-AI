/// Shared "is my work saved?" indicator.
///
/// Rule 19 of the UX brief asks for `syncing` and `stale` as first-class
/// states; rule 35 asks that *sync pending* be distinguishable from an
/// outright failure. Both signals already exist — `SyncState` from
/// `syncEngineProvider`, and `parkedOutboxCountProvider` for changes the
/// server has refused often enough to be set aside — but the only thing
/// rendering them was one widget inside chat. Everywhere else in the app, a
/// change sitting unsynced looked exactly like a change that had saved.
///
/// Lives in `core/sync/` rather than `core/design_system/` for the same
/// reason `AuraFailureView` lives in `core/network/`: the design system knows
/// how a status pill should *look*, and has no business knowing what a parked
/// outbox row is.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../design_system/aura_palette.dart';
import '../design_system/tokens.dart';
import '../../features/sync/sync_engine.dart';
import 'sync_client.dart';

/// What the indicator is currently saying, after folding the parked-outbox
/// count into [SyncState].
///
/// `pendingRefused` is the one that did not exist as a state before: the
/// engine reports `synced` because it has nothing left to try, while changes
/// the server kept refusing sit parked. Reporting that as "Synced" is untrue
/// in the way that matters most — the user believes their work is on the
/// server when it is not.
enum AuraSyncPhase { synced, syncing, offline, failed, pendingRefused }

/// Folds the two signals into one phase. Pure, so the precedence between
/// "parked changes exist" and whatever the engine last reported is testable
/// without pumping a frame.
AuraSyncPhase auraSyncPhaseFor(SyncState state, int parkedCount) {
  switch (state) {
    case SyncState.syncing:
      return AuraSyncPhase.syncing;
    case SyncState.offline:
      return AuraSyncPhase.offline;
    case SyncState.error:
      return AuraSyncPhase.failed;
    case SyncState.synced:
      // Parked rows only override a *settled* sync. While syncing or offline,
      // the live state is the more useful thing to show.
      return parkedCount > 0 ? AuraSyncPhase.pendingRefused : AuraSyncPhase.synced;
  }
}

/// The sentence the indicator shows and announces.
String auraSyncLabelFor(AuraSyncPhase phase, int parkedCount) {
  return switch (phase) {
    AuraSyncPhase.synced => 'Synced',
    AuraSyncPhase.syncing => 'Syncing',
    AuraSyncPhase.offline => 'Offline',
    // Not "Error": naming the thing that failed is what tells the user
    // whether their work is at risk.
    AuraSyncPhase.failed => 'Sync failed',
    AuraSyncPhase.pendingRefused =>
      parkedCount == 1 ? '1 change not synced' : '$parkedCount changes not synced',
  };
}

IconData auraSyncIconFor(AuraSyncPhase phase) => switch (phase) {
      AuraSyncPhase.synced => Icons.cloud_done_outlined,
      AuraSyncPhase.syncing => Icons.sync,
      AuraSyncPhase.offline => Icons.cloud_off_outlined,
      AuraSyncPhase.failed => Icons.sync_problem,
      AuraSyncPhase.pendingRefused => Icons.cloud_upload_outlined,
    };

class AuraSyncStatusView extends ConsumerWidget {
  const AuraSyncStatusView({super.key, this.showLabel = true, this.onSyncNow});

  /// Hidden on narrow surfaces. The label is still announced — see the
  /// [Semantics] below; this only drops the *visible* text.
  final bool showLabel;

  /// `null` leaves the indicator read-only.
  final VoidCallback? onSyncNow;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final syncState = ref.watch(syncEngineProvider);
    final parked = ref.watch(parkedOutboxCountProvider).value ?? 0;

    final phase = auraSyncPhaseFor(syncState, parked);
    final label = auraSyncLabelFor(phase, parked);
    final isSyncing = phase == AuraSyncPhase.syncing;

    final colour = switch (phase) {
      AuraSyncPhase.synced => palette.success,
      AuraSyncPhase.syncing => palette.pending,
      AuraSyncPhase.offline => palette.textSecondary,
      AuraSyncPhase.failed => palette.danger,
      AuraSyncPhase.pendingRefused => palette.warning,
    };

    return Semantics(
      // Whether the user's work is on the server is exactly the kind of
      // change worth announcing when it happens, rather than leaving them to
      // go and look.
      liveRegion: true,
      label: 'Sync status: $label',
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: 4),
        decoration: BoxDecoration(
          color: palette.surfaceVariant,
          border: Border.all(color: palette.border),
          borderRadius: BorderRadius.circular(AuraRadius.pill),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (showLabel) ...[
              Text(
                label,
                style: Theme.of(context).textTheme.labelSmall?.copyWith(
                      color: palette.textSecondary,
                    ),
              ),
              const SizedBox(width: AuraSpacing.xs),
            ],
            // The glyph differs per phase, not just its colour: with the
            // label hidden this is the only visible signal, and a tinted dot
            // would leave a colour-blind user with nothing. The previous
            // version rendered exactly that dot.
            if (isSyncing)
              SizedBox(
                width: 14,
                height: 14,
                child: CircularProgressIndicator(
                  strokeWidth: 1.5,
                  valueColor: AlwaysStoppedAnimation<Color>(colour),
                ),
              )
            else
              IconButton(
                tooltip: onSyncNow == null ? label : '$label. Sync now',
                onPressed: onSyncNow,
                style: IconButton.styleFrom(
                  minimumSize: const Size(28, 28),
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  padding: EdgeInsets.zero,
                  visualDensity: VisualDensity.compact,
                ),
                icon: Icon(auraSyncIconFor(phase), color: colour, size: 14),
              ),
          ],
        ),
      ),
    );
  }
}
