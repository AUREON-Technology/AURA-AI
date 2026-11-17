import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../design_system/design_system.dart';
import 'engineer_workspaces.dart';

/// Panel listing the engineer agent's persistent workspaces.
///
/// Exists because a workspace is server-side state with a deadline on it, and until now the
/// only way to know one existed was to ask the agent. Three things are worth a user's attention
/// and all three are shown: which repository each workspace holds, whether it is running, and
/// how long before it is deleted for inactivity.
class EngineerWorkspacesPanel extends ConsumerWidget {
  const EngineerWorkspacesPanel({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(engineerWorkspacesProvider);

    return async.when(
      loading: () => const AuraSkeleton(height: 96),
      // A failed fetch is an error, not "not enabled" (INT-025): the client used to fold every
      // failure into an empty, disabled list. Kept quiet -- this is a secondary panel -- but it
      // says the right thing and offers a retry.
      error: (_, _) => AuraEmptyState(
        icon: Icons.cloud_off_outlined,
        title: 'Workspaces unavailable',
        message: 'Could not load your workspaces. Your work is not lost -- try again shortly.',
        actionLabel: 'Retry',
        onAction: () => ref.invalidate(engineerWorkspacesProvider),
      ),
      data: (list) => _body(context, ref, list),
    );
  }

  Widget _body(BuildContext context, WidgetRef ref, EngineerWorkspaceList list) {
    if (!list.enabled) {
      // Not an error: this deployment simply has not switched the engineer agent on.
      return const AuraEmptyState(
        icon: Icons.terminal_outlined,
        title: 'Workspaces are not enabled',
        message: 'This server does not run the engineer agent’s sandboxes.',
      );
    }
    if (list.workspaces.isEmpty) {
      return AuraEmptyState(
        icon: Icons.folder_off_outlined,
        title: 'No workspaces yet',
        message: 'Ask the engineer agent to clone a repository and one will appear here. '
            'Workspaces are removed after ${list.ttlHours.round()} hours without activity.',
      );
    }

    final now = DateTime.now();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const AuraSectionHeader(title: 'Engineer workspaces'),
        for (final workspace in list.workspaces)
          Padding(
            padding: const EdgeInsets.only(bottom: AuraSpacing.md),
            child: _WorkspaceCard(workspace: workspace, now: now),
          ),
      ],
    );
  }
}

class _WorkspaceCard extends ConsumerWidget {
  const _WorkspaceCard({required this.workspace, required this.now});

  final EngineerWorkspace workspace;
  final DateTime now;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);

    return AuraCard(
      semanticLabel: 'Workspace for ${workspace.repo}',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  workspace.repo,
                  style: Theme.of(context).textTheme.titleMedium,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              _StatusChip(running: workspace.isRunning),
            ],
          ),
          const SizedBox(height: AuraSpacing.sm),
          Text(
            _expiryLabel(workspace, now),
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                  color: _isUrgent(workspace, now) ? palette.danger : palette.textSecondary,
                ),
          ),
          if (workspace.branch != null) ...[
            const SizedBox(height: AuraSpacing.xs),
            Text(
              'Branch ${workspace.branch}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
          const SizedBox(height: AuraSpacing.md),
          Align(
            alignment: AlignmentDirectional.centerEnd,
            child: AuraButton(
              label: 'Delete',
              icon: Icons.delete_outline,
              variant: AuraButtonVariant.ghost,
              onPressed: () => _confirmDelete(context, ref),
            ),
          ),
        ],
      ),
    );
  }

  /// Deleting throws away a checkout including anything uncommitted, and the server will not ask
  /// again -- so the confirmation says what is lost rather than "are you sure?".
  Future<void> _confirmDelete(BuildContext context, WidgetRef ref) async {
    var confirmed = false;
    await AuraDialog.show<void>(
      context: context,
      title: 'Delete this workspace?',
      confirmLabel: 'Delete',
      cancelLabel: 'Keep',
      confirmDestructive: true,
      onConfirm: () => confirmed = true,
      child: Text(
        'The checkout of ${workspace.repo} and any uncommitted changes in it will be removed. '
        'Anything already pushed is unaffected.',
      ),
    );
    if (!confirmed || !context.mounted) return;

    final ok = await ref.read(engineerWorkspacesClientProvider).destroy(workspace.id);
    if (!context.mounted) return;
    AuraSnackbar.show(
      context,
      message: ok ? 'Workspace deleted.' : 'Could not delete that workspace.',
      kind: ok ? AuraSnackbarKind.success : AuraSnackbarKind.error,
    );
    if (ok) ref.invalidate(engineerWorkspacesProvider);
  }

  static bool _isUrgent(EngineerWorkspace workspace, DateTime now) =>
      workspace.timeUntilExpiry(now).inHours < 12;

  static String _expiryLabel(EngineerWorkspace workspace, DateTime now) {
    final left = workspace.timeUntilExpiry(now);
    if (left.isNegative) return 'Due to be removed';
    if (left.inHours < 1) return 'Removed in under an hour unless used';
    if (left.inHours < 24) return 'Removed in ${left.inHours}h unless used';
    return 'Removed in ${left.inDays}d unless used';
  }
}

class _StatusChip extends StatelessWidget {
  const _StatusChip({required this.running});

  final bool running;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    // "Running" and "Idle" rather than the server's hot/warm: the distinction that matters to
    // the user is only whether their work is still there, and it always is.
    final color = running ? palette.success : palette.textSecondary;
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AuraSpacing.sm,
        vertical: AuraSpacing.xs,
      ),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(AuraRadius.sm),
      ),
      child: Text(
        running ? 'Running' : 'Idle',
        style: Theme.of(context).textTheme.labelSmall?.copyWith(color: color),
      ),
    );
  }
}
