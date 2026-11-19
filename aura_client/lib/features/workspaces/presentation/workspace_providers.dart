// Shared providers and small helpers for the workspace screens (SaaS P1-F2).

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/design_system/design_system.dart';
import '../../../core/network/aura_failure.dart';
import '../../../core/workspaces/active_workspace.dart';
import '../../../l10n/l10n.dart';
import '../data/workspace_models.dart';
import '../data/workspaces_client.dart';

final workspacesListProvider = FutureProvider.autoDispose<List<WorkspaceSummary>>((ref) async {
  final listed = await ref.read(workspacesClientProvider).list();
  await ref.read(activeWorkspaceProvider.notifier).reconcile(listed);
  return listed;
});

final membersProvider = FutureProvider.autoDispose.family<List<Member>, String>(
  (ref, id) => ref.read(workspacesClientProvider).members(id),
);

final invitesProvider = FutureProvider.autoDispose.family<List<InviteSummary>, String>(
  (ref, id) => ref.read(workspacesClientProvider).invites(id),
);

/// The workspace whose plan, billing and shared chats the screens show: the active one, or the
/// person's personal workspace when none is chosen.
final currentWorkspaceProvider = FutureProvider.autoDispose<WorkspaceSummary?>((ref) async {
  final active = ref.watch(activeWorkspaceProvider);
  if (active != null) return active;
  final all = await ref.watch(workspacesListProvider.future);
  for (final ws in all) {
    if (!ws.isTeam) return ws;
  }
  return all.isEmpty ? null : all.first;
});

String roleLabel(BuildContext context, String role) => switch (role) {
      'owner' => context.l10n.workspaceRoleOwner,
      'admin' => context.l10n.workspaceRoleAdmin,
      _ => context.l10n.workspaceRoleMember,
    };

/// A plan's name in the UI language, from the server's slug (F55). An unknown slug (a plan
/// added on the server later) is shown as sent rather than hidden.
String planLabel(BuildContext context, String plan) => switch (plan) {
      'free' => context.l10n.planNameFree,
      'pro' => context.l10n.planNamePro,
      'team' => context.l10n.planNameTeam,
      _ => plan,
    };

/// A subscription status in the UI language, from the server's slug
/// (`aura_core/domain/billing.py` `SubscriptionStatus`).
String subscriptionStatusLabel(BuildContext context, String status) => switch (status) {
      'pending' => context.l10n.subscriptionStatusPending,
      'active' => context.l10n.subscriptionStatusActive,
      'past_due' => context.l10n.subscriptionStatusPastDue,
      'grace' => context.l10n.subscriptionStatusGrace,
      'canceled' => context.l10n.subscriptionStatusCanceled,
      'expired' => context.l10n.subscriptionStatusExpired,
      _ => status,
    };

String workspaceName(BuildContext context, WorkspaceSummary ws) =>
    ws.isTeam ? ws.name : context.l10n.workspacePersonal;

/// A failed call, in the plain words [AuraFailure] already has for it.
void showFailure(BuildContext context, Object error) {
  final failure = AuraFailure.from(error);
  AuraSnackbar.show(context, message: '${failure.title}. ${failure.message}', kind: AuraSnackbarKind.error);
}

Future<bool> confirm(BuildContext context, {required String title, required String body, required String action, bool destructive = false}) async {
  final answer = await showDialog<bool>(
    context: context,
    builder: (dialogContext) => AuraDialog(
      title: title,
      confirmLabel: action,
      cancelLabel: context.l10n.cancel,
      confirmDestructive: destructive,
      onConfirm: () => Navigator.of(dialogContext).pop(true),
      child: Text(body),
    ),
  );
  return answer == true;
}
