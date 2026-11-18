import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/design_system/design_system.dart';
import '../../../core/workspaces/active_workspace.dart';
import '../../../l10n/l10n.dart';
import 'workspace_switcher.dart';

/// The settings row showing which workspace the app is in; opens the switcher.
class WorkspaceTile extends ConsumerWidget {
  const WorkspaceTile({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final active = ref.watch(activeWorkspaceProvider);
    return AuraListTile(
      key: const Key('workspace-tile'),
      icon: active?.isTeam == true ? Icons.groups_outlined : Icons.workspaces_outlined,
      title: context.l10n.workspaceTileTitle,
      subtitle: active?.isTeam == true ? active!.name : context.l10n.workspacePersonal,
      onTap: () => showWorkspaceSwitcher(context),
    );
  }
}

/// "Team chats" while a team is active (SaaS P1-F2).
class TeamChatsTile extends ConsumerWidget {
  const TeamChatsTile({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final active = ref.watch(activeWorkspaceProvider);
    if (active == null || !active.isTeam) return const SizedBox.shrink();
    return AuraListTile(
      key: const Key('team-chats-tile'),
      icon: Icons.forum_outlined,
      title: context.l10n.teamChatsTitle,
      onTap: () => context.push('/team-chats'),
    );
  }
}
