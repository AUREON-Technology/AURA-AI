/// Shared "grant hostd access to this workspace" flow.
///
/// hostd grants a workspace *root*, never an arbitrary subfolder, so a subdirectory tile hitting
/// the ungranted-root case (`ExplorerService.listDirectory`'s `isAccessDenied`) is fixed the exact
/// same way the top-level Explorer error view is: grant the workspace root. Both live in
/// `explorer_panel.dart` and `explorer_tree_tile.dart` respectively, which already import each
/// other, so this shared flow gets its own file rather than living in either (or in
/// `explorer_service.dart`, which otherwise has no Flutter UI dependency).
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart' hide HostdRoot;
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Confirms with the human, grants [root] to hostd, records it locally, then calls [onGranted] so
/// the caller can re-fetch. Mirrors `WorkspaceManagerPage._openFolder`'s own grant call.
Future<void> grantWorkspaceAccess(
  BuildContext context,
  WidgetRef ref,
  String root, {
  VoidCallback? onGranted,
}) async {
  final confirmed = await AuraDialog.show<bool>(
    context: context,
    title: context.l10n.explorerGrantTitle,
    child: Text(context.l10n.explorerGrantBody(root)),
    confirmLabel: context.l10n.explorerGrant,
    cancelLabel: context.l10n.ideNotNow,
    onConfirm: () => Navigator.of(context).pop(true),
  );
  if (confirmed != true || !context.mounted) return;
  try {
    await ref.read(hostdControlClientProvider).grantRoot(root);
    ref.read(workspaceNotifierProvider.notifier).updateHostdRoots([
      ...ref.read(workspaceNotifierProvider).hostdRoots,
      HostdRoot(path: root, trusted: false),
    ]);
    onGranted?.call();
    if (context.mounted) {
      AuraSnackbar.show(context, message: context.l10n.explorerAccessGranted, kind: AuraSnackbarKind.success);
    }
  } catch (e) {
    if (context.mounted) {
      AuraSnackbar.show(context, message: context.l10n.explorerGrantFailed(e.toString()), kind: AuraSnackbarKind.error);
    }
  }
}
