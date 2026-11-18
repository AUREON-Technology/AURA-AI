import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/design_system/design_system.dart';
import '../../../core/network/aura_failure.dart';
import '../../../core/network/aura_failure_view.dart';
import '../../../core/workspaces/active_workspace.dart';
import '../../../l10n/l10n.dart';
import 'create_team_dialog.dart';
import 'workspace_providers.dart';

Future<void> showWorkspaceSwitcher(BuildContext context) => showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (_) => const WorkspaceSwitcher(),
    );

/// Every workspace the person belongs to; tapping one makes it active.
class WorkspaceSwitcher extends ConsumerWidget {
  const WorkspaceSwitcher({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final l10n = context.l10n;
    final listed = ref.watch(workspacesListProvider);
    final active = ref.watch(activeWorkspaceProvider);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(AuraSpacing.lg),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(l10n.workspaceSwitcherTitle, style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: AuraSpacing.md),
            listed.when(
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (error, _) => AuraFailureView(
                failure: AuraFailure.from(error),
                compact: true,
                onRetry: () => ref.invalidate(workspacesListProvider),
              ),
              data: (workspaces) => Column(
                children: [
                  for (final ws in workspaces)
                    AuraListTile(
                      key: Key('workspace-${ws.id}'),
                      icon: ws.isTeam ? Icons.groups_outlined : Icons.person_outline,
                      title: workspaceName(context, ws),
                      subtitle: roleLabel(context, ws.role),
                      trailing: (active?.id ?? _personalId(workspaces)) == ws.id ? const Icon(Icons.check) : null,
                      onTap: () async {
                        final notifier = ref.read(activeWorkspaceProvider.notifier);
                        ws.isTeam ? await notifier.select(ws) : await notifier.clear();
                        if (context.mounted) Navigator.of(context).pop();
                      },
                    ),
                ],
              ),
            ),
            const SizedBox(height: AuraSpacing.md),
            if (active != null && active.isTeam)
              AuraButton(
                label: l10n.workspaceTeamSettings,
                variant: AuraButtonVariant.secondary,
                icon: Icons.settings_outlined,
                onPressed: () {
                  Navigator.of(context).pop();
                  context.push('/workspaces/${active.id}');
                },
              ),
            const SizedBox(height: AuraSpacing.sm),
            AuraButton(
              label: l10n.workspaceCreateTeam,
              variant: AuraButtonVariant.ghost,
              icon: Icons.add,
              onPressed: () async {
                final team = await showCreateTeamDialog(context, ref);
                if (team != null && context.mounted) Navigator.of(context).pop();
              },
            ),
          ],
        ),
      ),
    );
  }

  static String? _personalId(List<WorkspaceSummary> all) {
    for (final ws in all) {
      if (!ws.isTeam) return ws.id;
    }
    return null;
  }
}
