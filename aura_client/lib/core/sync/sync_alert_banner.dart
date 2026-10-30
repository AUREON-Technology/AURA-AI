/// Shell-level warning that work is sitting unsaved on this device.
///
/// `AuraSyncStatusView` shows sync state where there is chrome to put a pill
/// in; chat is the only screen that has any. So on Tasks, Finance or Calendar
/// a change the server has refused looked exactly like a change that saved —
/// which is the one direction this particular mistake must not point.
///
/// This fills that gap from the shared shell, and only when there is something
/// to say: at zero parked changes it renders nothing at all, so it is not
/// permanent chrome.
///
/// It watches **only** `parkedOutboxCountProvider`, not `syncEngineProvider`,
/// and that is deliberate rather than incidental: merely building the engine
/// starts a 60-second `Timer.periodic` (see `SyncEngine.build`), which makes it
/// unusable in any widget test that does not override it — `overflow_layout_test`
/// already carries such an override for that reason. Putting the engine behind
/// the whole app's shell would have pushed that timer into every test that
/// pumps a route. The parked count is a plain database watch, and it is also
/// the signal that matters here: a parked change is work that is not on the
/// server, whatever the engine last reported.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../design_system/components/aura_banner.dart';
import '../design_system/components/aura_empty_state.dart' show AuraStateTone;
import 'sync_client.dart';

class SyncAlertBanner extends ConsumerWidget {
  const SyncAlertBanner({super.key, this.onReview});

  /// Optional "what do I do about it" action. Omitted, the banner still
  /// reports the condition — saying nothing would be worse than saying it
  /// without a button.
  final VoidCallback? onReview;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // A failed read means we do not know, which is not the same as zero, but
    // it is not grounds for alarming the user either.
    final parked = ref.watch(parkedOutboxCountProvider).value ?? 0;
    if (parked == 0) return const SizedBox.shrink();

    return AuraBanner(
      icon: Icons.cloud_upload_outlined,
      tone: AuraStateTone.warning,
      message: parked == 1
          ? "1 change hasn't reached the server. It's saved on this device."
          : "$parked changes haven't reached the server. They're saved on this device.",
      semanticLabel: parked == 1
          ? "1 change has not reached the server. It is saved on this device."
          : "$parked changes have not reached the server. They are saved on this device.",
      actionLabel: onReview == null ? null : 'Review',
      onAction: onReview,
    );
  }
}
