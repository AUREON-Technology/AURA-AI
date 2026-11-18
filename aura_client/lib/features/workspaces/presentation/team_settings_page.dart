import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/config/app_links.dart';
import '../../../core/design_system/design_system.dart';
import '../../../core/network/aura_failure.dart';
import '../../../core/network/aura_failure_view.dart';
import '../../../core/workspaces/active_workspace.dart';
import '../../../l10n/l10n.dart';
import '../../auth/presentation/auth_provider.dart';
import '../data/workspace_models.dart';
import '../data/workspaces_client.dart';
import 'workspace_providers.dart';

/// A team's members, invites and roles (SaaS P1-F2, ADR-034). What each person may do is the
/// server's decision; this page only hides controls their role would be refused.
class TeamSettingsPage extends ConsumerWidget {
  const TeamSettingsPage({super.key, required this.workspaceId});

  final String workspaceId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final listed = ref.watch(workspacesListProvider);
    return Scaffold(
      appBar: AppBar(title: Text(context.l10n.workspaceTeamSettings)),
      body: listed.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) => AuraFailureView(
          failure: AuraFailure.from(error),
          onRetry: () => ref.invalidate(workspacesListProvider),
        ),
        data: (all) {
          final ws = all.where((w) => w.id == workspaceId).firstOrNull;
          if (ws == null) {
            return AuraFailureView(failure: AuraFailure.from(StateError('not a member of this workspace')));
          }
          return _TeamBody(workspace: ws);
        },
      ),
    );
  }
}

class _TeamBody extends ConsumerWidget {
  const _TeamBody({required this.workspace});

  final WorkspaceSummary workspace;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final id = workspace.id;
    final me = ref.watch(authProvider).value?.id;
    final members = ref.watch(membersProvider(id));
    final invites = workspace.canManage ? ref.watch(invitesProvider(id)) : null;
    final client = ref.read(workspacesClientProvider);

    Future<void> run(Future<void> Function() action) async {
      try {
        await action();
        ref.invalidate(membersProvider(id));
        ref.invalidate(invitesProvider(id));
        ref.invalidate(workspacesListProvider);
      } catch (error) {
        if (context.mounted) showFailure(context, error);
      }
    }

    return ListView(
      padding: const EdgeInsets.all(AuraSpacing.lg),
      children: [
        Text(workspace.name, style: Theme.of(context).textTheme.headlineSmall),
        Text(roleLabel(context, workspace.role)),
        const SizedBox(height: AuraSpacing.lg),
        AuraSectionHeader(
          title: l10n.teamMembersSection,
          actionLabel: workspace.canManage ? l10n.teamInviteAction : null,
          onAction: workspace.canManage ? () => _invite(context, ref) : null,
        ),
        if (!workspace.canManage) Text(l10n.teamReadOnlyNote, key: const Key('team-read-only')),
        members.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (error, _) => AuraFailureView(failure: AuraFailure.from(error), compact: true),
          data: (list) => AuraCard(
            padding: EdgeInsets.zero,
            child: Column(children: [
              for (final m in list)
                AuraListTile(
                  key: Key('member-${m.userId}'),
                  icon: Icons.person_outline,
                  title: m.userId == me ? l10n.teamYou : (m.email ?? m.userId),
                  subtitle: roleLabel(context, m.role),
                  trailing: _memberActions(context, m, me, run, client),
                ),
            ]),
          ),
        ),
        if (invites != null) ...[
          const SizedBox(height: AuraSpacing.lg),
          AuraSectionHeader(title: l10n.teamInvitesSection),
          invites.when(
            loading: () => const SizedBox.shrink(),
            error: (error, _) => AuraFailureView(failure: AuraFailure.from(error), compact: true),
            data: (list) => Column(children: [
              for (final i in list)
                AuraListTile(
                  key: Key('invite-${i.inviteId}'),
                  icon: Icons.mail_outline,
                  title: i.email,
                  subtitle: roleLabel(context, i.role),
                  trailing: TextButton(
                    onPressed: () => run(() => client.revokeInvite(workspace.id, i.inviteId)),
                    child: Text(l10n.teamRevokeInvite),
                  ),
                ),
            ]),
          ),
        ],
        const SizedBox(height: AuraSpacing.xl),
        if (workspace.isOwner)
          AuraButton(
            key: const Key('team-delete'),
            label: l10n.teamDelete,
            variant: AuraButtonVariant.destructive,
            onPressed: () async {
              if (!await confirm(context, title: l10n.teamDelete, body: l10n.teamDeleteConfirm(workspace.name),
                  action: l10n.teamDelete, destructive: true)) {
                return;
              }
              await run(() => client.deleteTeam(workspace.id));
              await ref.read(activeWorkspaceProvider.notifier).clear();
              if (context.mounted) context.pop();
            },
          )
        else if (me != null)
          AuraButton(
            key: const Key('team-leave'),
            label: l10n.teamLeave,
            variant: AuraButtonVariant.secondary,
            onPressed: () async {
              if (!await confirm(context, title: l10n.teamLeave, body: l10n.teamLeaveConfirm(workspace.name),
                  action: l10n.teamLeave, destructive: true)) {
                return;
              }
              await run(() => client.remove(workspace.id, me));
              await ref.read(activeWorkspaceProvider.notifier).clear();
              if (context.mounted) context.pop();
            },
          ),
      ],
    );
  }

  Widget? _memberActions(BuildContext context, Member m, String? me,
      Future<void> Function(Future<void> Function()) run, WorkspacesClient client) {
    final l10n = context.l10n;
    if (m.userId == me || m.role == 'owner' || !workspace.canManage) return null;
    // An admin manages members, never other admins (the server refuses that too).
    if (!workspace.isOwner && m.role != 'member') return null;
    final who = m.email ?? m.userId;
    return PopupMenuButton<String>(
      key: Key('member-menu-${m.userId}'),
      onSelected: (action) async {
        switch (action) {
          case 'admin':
          case 'member':
            await run(() => client.changeRole(workspace.id, m.userId, action));
          case 'owner':
            if (await confirm(context, title: l10n.teamMakeOwner, body: l10n.teamMakeOwnerConfirm(who), action: l10n.teamMakeOwner)) {
              await run(() => client.transfer(workspace.id, m.userId));
            }
          case 'remove':
            if (context.mounted &&
                await confirm(context, title: l10n.teamRemove, body: l10n.teamRemoveConfirm(who), action: l10n.teamRemove, destructive: true)) {
              await run(() => client.remove(workspace.id, m.userId));
            }
        }
      },
      itemBuilder: (_) => [
        if (workspace.isOwner && m.role == 'member') PopupMenuItem(value: 'admin', child: Text(l10n.teamMakeAdmin)),
        if (workspace.isOwner && m.role == 'admin') PopupMenuItem(value: 'member', child: Text(l10n.teamMakeMember)),
        if (workspace.isOwner) PopupMenuItem(value: 'owner', child: Text(l10n.teamMakeOwner)),
        PopupMenuItem(value: 'remove', child: Text(l10n.teamRemove)),
      ],
    );
  }

  Future<void> _invite(BuildContext context, WidgetRef ref) async {
    final l10n = context.l10n;
    final answer = await showDialog<(String, String)>(context: context, builder: (_) => const _InviteDialog());
    if (answer == null || answer.$1.isEmpty || !context.mounted) return;
    final (address, role) = answer;
    try {
      final created = await ref.read(workspacesClientProvider).invite(workspace.id, address, role);
      ref.invalidate(invitesProvider(workspace.id));
      final token = created.token;
      if (token == null || !context.mounted) return;
      final link = inviteLinkFor(token);
      await showDialog<void>(
        context: context,
        builder: (dialogContext) => AuraDialog(
          title: l10n.teamInviteSentTitle,
          confirmLabel: l10n.teamCopyLink,
          cancelLabel: l10n.cancel,
          onConfirm: () async {
            await Clipboard.setData(ClipboardData(text: link));
            if (dialogContext.mounted) Navigator.of(dialogContext).pop();
            if (context.mounted) AuraSnackbar.show(context, message: l10n.teamLinkCopied, kind: AuraSnackbarKind.success);
          },
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l10n.teamInviteSentBody(address)),
            const SizedBox(height: AuraSpacing.sm),
            SelectableText(link, key: const Key('invite-link')),
          ]),
        ),
      );
    } catch (error) {
      if (context.mounted) showFailure(context, error);
    }
  }
}


/// Email and role for a new invite. Owns its controller (see `_CreateTeamDialog`).
class _InviteDialog extends StatefulWidget {
  const _InviteDialog();

  @override
  State<_InviteDialog> createState() => _InviteDialogState();
}

class _InviteDialogState extends State<_InviteDialog> {
  final _email = TextEditingController();
  String _role = 'member';

  @override
  void dispose() {
    _email.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return AuraDialog(
      title: l10n.teamInviteAction,
      confirmLabel: l10n.teamInviteAction,
      cancelLabel: l10n.cancel,
      onConfirm: () => Navigator.of(context).pop((_email.text.trim(), _role)),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        AuraTextField(
          key: const Key('invite-email'),
          controller: _email,
          label: l10n.teamInviteEmailLabel,
          keyboardType: TextInputType.emailAddress,
        ),
        const SizedBox(height: AuraSpacing.sm),
        SegmentedButton<String>(
          segments: [
            ButtonSegment(value: 'member', label: Text(l10n.workspaceRoleMember)),
            ButtonSegment(value: 'admin', label: Text(l10n.workspaceRoleAdmin)),
          ],
          selected: {_role},
          onSelectionChanged: (s) => setState(() => _role = s.first),
        ),
      ]),
    );
  }
}
