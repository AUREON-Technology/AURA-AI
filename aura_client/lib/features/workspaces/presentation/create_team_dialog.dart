import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/design_system/design_system.dart';
import '../../../core/workspaces/active_workspace.dart';
import '../../../l10n/l10n.dart';
import '../data/workspaces_client.dart';
import 'workspace_providers.dart';

/// Asks for a name, creates the team and makes it active. Returns it, or null when cancelled.
Future<WorkspaceSummary?> showCreateTeamDialog(BuildContext context, WidgetRef ref) async {
  final name = await showDialog<String>(context: context, builder: (_) => const _CreateTeamDialog());
  if (name == null || name.isEmpty || !context.mounted) return null;
  try {
    final team = await ref.read(workspacesClientProvider).createTeam(name);
    await ref.read(activeWorkspaceProvider.notifier).select(team);
    ref.invalidate(workspacesListProvider);
    return team;
  } catch (error) {
    if (context.mounted) showFailure(context, error);
    return null;
  }
}


/// Owns its controller, so the field outlives the dialog's closing animation.
class _CreateTeamDialog extends StatefulWidget {
  const _CreateTeamDialog();

  @override
  State<_CreateTeamDialog> createState() => _CreateTeamDialogState();
}

class _CreateTeamDialogState extends State<_CreateTeamDialog> {
  final _name = TextEditingController();

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return AuraDialog(
      title: l10n.workspaceCreateTeam,
      confirmLabel: l10n.teamCreateAction,
      cancelLabel: l10n.cancel,
      onConfirm: () => Navigator.of(context).pop(_name.text.trim()),
      child: AuraTextField(
        key: const Key('create-team-name'),
        controller: _name,
        label: l10n.teamNameLabel,
        onSubmitted: (value) => Navigator.of(context).pop(value.trim()),
      ),
    );
  }
}
