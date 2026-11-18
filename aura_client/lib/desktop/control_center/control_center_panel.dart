/// "Agent Review" panel for Desktop IDE (D9 -- Multi-Agent Control Center).
///
/// Shown in the primary sidebar when `ActivityBar`'s "AURA" item is selected
/// (`desktop_shell.dart`'s switch on `LayoutState.primarySidebarPanel`, closing the D6 stub).
/// One card per isolated per-task worktree (`GET /tasks`), each with an inline branch-range diff
/// (reusing `SandboxDiffView`) and the two human-only actions D9 adds on top of the existing D5/D6
/// write-gating: Merge and Discard. Neither action is reachable via the agent-facing RPC surface
/// -- see `control_center_notifier.dart`'s own doc comments on `merge`/`discard`.
///
/// Note on scope: `aura_hostd` does not track a `status` field
/// (`in_progress`/`awaiting_review`/`merged`/`discarded`) for a task -- it only knows whether a
/// worktree currently exists. Rather than fabricate a status the daemon can't back up, every
/// listed task is shown simply as an open isolated task; it disappears from the list the moment
/// it's merged-and-discarded or discarded outright.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path/path.dart' as p;

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/control_center/control_center_notifier.dart';
import 'package:aura_client/features/chat/presentation/widgets/sandbox_diff_view.dart';
import 'package:aura_client/l10n/l10n.dart';

class ControlCenterPanel extends ConsumerStatefulWidget {
  const ControlCenterPanel({super.key});

  @override
  ConsumerState<ControlCenterPanel> createState() => _ControlCenterPanelState();
}

class _ControlCenterPanelState extends ConsumerState<ControlCenterPanel> {
  bool _refreshed = false;

  void _maybeRefresh() {
    if (_refreshed) return;
    _refreshed = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) ref.read(controlCenterNotifierProvider.notifier).refresh();
    });
  }

  Future<void> _confirmDiscard(ControlCenterTask task) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(dialogContext.l10n.controlCenterDiscardTitle),
        content: Text(dialogContext.l10n.controlCenterDiscardBody(task.branch, task.worktreePath)),
        actions: [
          TextButton(onPressed: () => Navigator.of(dialogContext).pop(false), child: Text(dialogContext.l10n.cancel)),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: AuraPalette.of(context).danger),
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: Text(dialogContext.l10n.controlCenterDiscard),
          ),
        ],
      ),
    );
    if (confirmed == true && mounted) {
      await ref.read(controlCenterNotifierProvider.notifier).discard(task.taskKey);
    }
  }

  Future<void> _merge(ControlCenterTask task, {bool squash = false}) async {
    final ok = await ref.read(controlCenterNotifierProvider.notifier).merge(task, squash: squash);
    if (!ok || !mounted) return;
    final discardAfter = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(dialogContext.l10n.controlCenterMergedTitle),
        content: Text(dialogContext.l10n.controlCenterMergedBody(task.branch)),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: Text(dialogContext.l10n.controlCenterKeepIt),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: Text(dialogContext.l10n.controlCenterDiscard),
          ),
        ],
      ),
    );
    if (discardAfter == true && mounted) {
      await ref.read(controlCenterNotifierProvider.notifier).discard(task.taskKey);
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    _maybeRefresh();
    final state = ref.watch(controlCenterNotifierProvider);

    return Container(
      color: palette.surface,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.all(AuraSpacing.md),
            child: Row(
              children: [
                Icon(Icons.auto_awesome_outlined, size: 16, color: palette.textSecondary),
                const SizedBox(width: AuraSpacing.xs),
                Expanded(
                  child: Text(
                    context.l10n.controlCenterTitle,
                    style: TextStyle(color: palette.textPrimary, fontWeight: FontWeight.w600),
                  ),
                ),
                if (state.loading)
                  const Padding(
                    padding: EdgeInsets.symmetric(horizontal: AuraSpacing.xs),
                    child: SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
                  ),
                IconButton(
                  icon: const Icon(Icons.refresh, size: 18),
                  tooltip: context.l10n.commonRefresh,
                  onPressed: () => ref.read(controlCenterNotifierProvider.notifier).refresh(),
                ),
              ],
            ),
          ),
          if (state.error != null)
            Container(
              width: double.infinity,
              margin: const EdgeInsets.symmetric(horizontal: AuraSpacing.md),
              padding: const EdgeInsets.all(AuraSpacing.sm),
              decoration: BoxDecoration(
                color: palette.danger.withValues(alpha: 0.1),
                borderRadius: BorderRadius.circular(AuraRadius.sm),
              ),
              child: Text(state.error!, style: TextStyle(color: palette.danger, fontSize: 12)),
            ),
          Expanded(
            child: state.tasks.isEmpty
                ? Center(
                    child: Padding(
                      padding: const EdgeInsets.all(AuraSpacing.lg),
                      child: Text(
                        context.l10n.controlCenterEmpty,
                        textAlign: TextAlign.center,
                        style: TextStyle(color: palette.textSecondary, fontSize: 12),
                      ),
                    ),
                  )
                : ListView(
                    padding: const EdgeInsets.symmetric(vertical: AuraSpacing.sm),
                    children: [
                      for (final task in state.tasks)
                        _TaskCard(
                          task: task,
                          palette: palette,
                          busy: state.busyTaskKey == task.taskKey,
                          expanded: state.expandedTaskKey == task.taskKey,
                          diffText: state.diffText,
                          hasConflict: state.conflictTaskKey == task.taskKey,
                          conflictedFiles: state.conflictedFiles,
                          onToggleDiff: () => ref.read(controlCenterNotifierProvider.notifier).toggleDiff(task),
                          onMerge: () => _merge(task),
                          onMergeSquash: () => _merge(task, squash: true),
                          onDiscard: () => _confirmDiscard(task),
                        ),
                    ],
                  ),
          ),
        ],
      ),
    );
  }
}

class _TaskCard extends StatelessWidget {
  const _TaskCard({
    required this.task,
    required this.palette,
    required this.busy,
    required this.expanded,
    required this.diffText,
    required this.hasConflict,
    required this.conflictedFiles,
    required this.onToggleDiff,
    required this.onMerge,
    required this.onMergeSquash,
    required this.onDiscard,
  });

  final ControlCenterTask task;
  final AuraPalette palette;
  final bool busy;
  final bool expanded;
  final String? diffText;
  final bool hasConflict;
  final List<String> conflictedFiles;
  final VoidCallback onToggleDiff;
  final VoidCallback onMerge;
  final VoidCallback onMergeSquash;
  final VoidCallback onDiscard;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: AuraSpacing.md, vertical: AuraSpacing.xs),
      padding: const EdgeInsets.all(AuraSpacing.sm),
      decoration: BoxDecoration(
        border: Border.all(color: palette.border),
        borderRadius: BorderRadius.circular(AuraRadius.sm),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Icon(Icons.call_split, size: 14, color: palette.textSecondary),
              const SizedBox(width: AuraSpacing.xs),
              Expanded(
                child: Text(
                  task.branch,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: palette.textPrimary, fontWeight: FontWeight.w600, fontSize: 13),
                ),
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text(
              p.basename(task.parentRoot),
              overflow: TextOverflow.ellipsis,
              style: TextStyle(color: palette.textSecondary, fontSize: 11),
            ),
          ),
          if (task.diffStat != null)
            Padding(
              padding: const EdgeInsets.only(top: AuraSpacing.xs),
              child: Text(
                task.diffStat!.trim().isEmpty ? context.l10n.controlCenterNoChanges : task.diffStat!.trim(),
                style: TextStyle(color: palette.textSecondary, fontSize: 11, fontFamily: 'Courier'),
              ),
            ),
          if (hasConflict)
            Container(
              margin: const EdgeInsets.only(top: AuraSpacing.xs),
              padding: const EdgeInsets.all(AuraSpacing.xs),
              decoration: BoxDecoration(
                color: palette.warning.withValues(alpha: 0.1),
                borderRadius: BorderRadius.circular(AuraRadius.sm),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    context.l10n.controlCenterMergeConflict,
                    style: TextStyle(color: palette.warning, fontSize: 11, fontWeight: FontWeight.w600),
                  ),
                  for (final file in conflictedFiles)
                    Text('  $file', style: TextStyle(color: palette.warning, fontSize: 11, fontFamily: 'Courier')),
                ],
              ),
            ),
          Padding(
            padding: const EdgeInsets.only(top: AuraSpacing.xs),
            child: Row(
              children: [
                TextButton(
                  onPressed: onToggleDiff,
                  child: Text(expanded ? context.l10n.controlCenterHideDiff : context.l10n.controlCenterViewDiff),
                ),
                const Spacer(),
                IconButton(
                  icon: const Icon(Icons.delete_outline, size: 16),
                  tooltip: context.l10n.controlCenterDiscard,
                  onPressed: busy ? null : onDiscard,
                ),
                TextButton(
                  onPressed: busy ? null : onMergeSquash,
                  child: Text(context.l10n.controlCenterSquashMerge),
                ),
                FilledButton(
                  onPressed: busy ? null : onMerge,
                  child: busy ? Text(context.l10n.controlCenterWorking) : Text(context.l10n.controlCenterMerge),
                ),
              ],
            ),
          ),
          if (expanded)
            diffText == null
                ? const Padding(
                    padding: EdgeInsets.symmetric(vertical: AuraSpacing.sm),
                    child: Center(child: SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))),
                  )
                : SandboxDiffView(diffText: diffText!),
        ],
      ),
    );
  }
}
