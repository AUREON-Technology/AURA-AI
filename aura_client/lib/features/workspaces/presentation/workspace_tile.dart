import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

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
