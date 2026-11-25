// Work that belongs to being signed in rather than to any screen (F1, F5).
//
// `AuraApp` listens to [signedInSessionProvider] for the life of the app. Signed out it does
// nothing; signed in it keeps the sync engine running (60 s background sync on every platform,
// not only where the mobile chat header happens to be on screen) and checks the saved team
// against the server once.
//
// Kept out of the shell's widgets on purpose (`core/sync/sync_alert_banner.dart`): a widget test
// that pumps a route does not build `AuraApp`, so it gets no timer from here.

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/workspaces/active_workspace.dart';
import '../engineering/data/agent_task_outbox.dart';
import '../auth/presentation/auth_provider.dart';
import '../workspaces/data/workspaces_client.dart';
import 'sync_engine.dart';

/// Listening to it is what runs the signed-in work. Built once: it only listens, so its
/// subscriptions are never torn down and re-made around a sign-in.
final signedInSessionProvider = Provider<void>((ref) {
  // Listened, so the engine is never paused: it rebuilds on sign-in and sign-out itself
  // (`SyncEngine.build`), starting and cancelling its timer.
  ref.listen(syncEngineProvider, (_, _) {});
  // Queued agent task submissions (row 91): replayed on sign-in, every 30 s and on app resume.
  ref.listen(agentTaskOutboxDriverProvider, (_, _) {});
  ref.listen(authProvider.select((auth) => auth.value?.id), (previous, next) {
    if (next != null && next != previous) unawaited(_reconcileActiveWorkspace(ref));
  }, fireImmediately: true);
});

/// A team saved on this device may have been left, deleted, or had our role changed elsewhere.
/// Until now only opening a workspace screen checked; every other request kept naming a team the
/// server refuses (F5). Best effort: offline, the per-request 403 healing still applies.
Future<void> _reconcileActiveWorkspace(Ref ref) async {
  try {
    ref.read(activeWorkspaceProvider);
    final active = ref.read(activeWorkspaceProvider.notifier);
    await active.ready;
    if (!ref.mounted || ref.read(activeWorkspaceProvider) == null) return;
    final listed = await ref.read(workspacesClientProvider).list();
    if (!ref.mounted) return;
    await active.reconcile(listed);
  } catch (e) {
    debugPrint('[Session] workspace check after sign-in skipped: $e');
  }
}
