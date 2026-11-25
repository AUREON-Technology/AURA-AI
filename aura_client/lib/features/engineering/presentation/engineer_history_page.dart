import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/ai/engineer/workspaces_panel.dart';
import 'package:aura_client/core/ai/engineer/engineer_workspaces.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';
import 'package:aura_client/features/chat/presentation/widgets/sandbox_diff_view.dart';
import 'package:aura_client/features/chat/presentation/widgets/sandbox_terminal_view.dart';
import 'package:aura_client/l10n/l10n.dart';

import '../data/engineer_run_repository.dart';

/// Durable history of everything the Engineer agent has done: PRs opened, branches created,
/// commits made, sandbox commands run -- across every conversation, not scoped to one chat.
/// Unlike the in-chat step log and confirmation card, this survives the conversation being
/// archived and a reinstall (it reads straight from the backend's `EngineerRunStore`, not local
/// Drift state).
///
/// Persistent workspaces (ADR-013) live above the run list: a workspace is server-side state
/// with a deadline, and this page is the only engineer surface reachable from Settings and the
/// persona picker.
class EngineerHistoryPage extends ConsumerStatefulWidget {
  const EngineerHistoryPage({
    super.key,
    @visibleForTesting this.isWeb = kIsWeb,
  });

  /// Whether to lay out as the web build does on a wide window: the run list beside the open
  /// run. A parameter only so a test can ask the web's answer on the test host.
  final bool isWeb;

  @override
  ConsumerState<EngineerHistoryPage> createState() =>
      _EngineerHistoryPageState();
}

class _EngineerHistoryPageState extends ConsumerState<EngineerHistoryPage> {
  /// The run open in the detail pane (wide web layout only; narrower screens use a sheet).
  String? _selectedRunId;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final runsAsync = ref.watch(engineerRunsProvider);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: palette.background,
        title: Text(context.l10n.settingsEngineerTitle),
        actions: [
          IconButton(
            key: const ValueKey('engineer-tasks-button'),
            tooltip: context.l10n.engineeringTasksTooltip,
            icon: const Icon(Icons.task_alt_outlined),
            onPressed: () => context.push('/engineer-tasks'),
          ),
          IconButton(
            key: const ValueKey('engineer-run-search-button'),
            tooltip: context.l10n.engineeringRunSearchTooltip,
            icon: const Icon(Icons.manage_search),
            onPressed: () => context.push('/engineer-runs'),
          ),
          IconButton(
            key: const ValueKey('engineer-usage-button'),
            tooltip: context.l10n.engineeringUsageTooltip,
            icon: const Icon(Icons.bar_chart_outlined),
            onPressed: () => context.push('/usage'),
          ),
          IconButton(
            tooltip: context.l10n.engineeringDesktopDaemonTooltip,
            icon: const Icon(Icons.dns_outlined),
            onPressed: () => context.push('/engineer-daemon'),
          ),
        ],
      ),
      body: LayoutBuilder(
        builder: (context, box) {
          final split = masterDetailSplits(box.maxWidth, isWeb: widget.isWeb);
          final list = _buildList(context, runsAsync, split: split);
          if (!split) return list;
          final runs = runsAsync.asData?.value ?? const <EngineerRun>[];
          EngineerRun? selected;
          for (final run in runs) {
            if (run.id == _selectedRunId) selected = run;
          }
          return AuraMasterDetail(
            masterWidth: 400,
            master: list,
            detail: selected == null
                ? AuraDetailPlaceholder(
                    icon: Icons.code_outlined,
                    title: context.l10n.webEngineerPaneTitle,
                    message: context.l10n.webEngineerPaneMessage,
                  )
                : SingleChildScrollView(
                    key: ValueKey(selected.id),
                    padding: const EdgeInsets.all(AuraSpacing.lg),
                    child: AuraReadableWidth(
                      enabled: true,
                      maxWidth: 760,
                      child: _EngineerRunDetailBody(run: selected),
                    ),
                  ),
          );
        },
      ),
    );
  }

  Widget _buildList(
    BuildContext context,
    AsyncValue<List<EngineerRun>> runsAsync, {
    required bool split,
  }) {
    return RefreshIndicator(
      onRefresh: () async {
        await Future.wait([
          ref.refresh(engineerWorkspacesProvider.future),
          ref.refresh(engineerRunsProvider.future),
        ]);
      },
      child: CustomScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        slivers: [
          const SliverPadding(
            padding: EdgeInsets.fromLTRB(
              AuraSpacing.md,
              AuraSpacing.md,
              AuraSpacing.md,
              0,
            ),
            sliver: SliverToBoxAdapter(child: EngineerWorkspacesPanel()),
          ),
          SliverPadding(
            padding: const EdgeInsets.all(AuraSpacing.md),
            sliver: runsAsync.when(
              data: (runs) {
                if (runs.isEmpty) {
                  return SliverToBoxAdapter(
                    child: AuraEmptyState(
                      icon: Icons.code_outlined,
                      title: context.l10n.engineeringNoRunsTitle,
                      message: context.l10n.engineeringNoRunsMessage,
                    ),
                  );
                }
                return SliverList.separated(
                  itemCount: runs.length,
                  separatorBuilder: (_, _) =>
                      const SizedBox(height: AuraSpacing.sm),
                  itemBuilder: (context, index) => _EngineerRunTile(
                    run: runs[index],
                    selected: split && runs[index].id == _selectedRunId,
                    onTap: split
                        ? () => setState(() => _selectedRunId = runs[index].id)
                        : () => _showRunSheet(context, runs[index]),
                  ),
                );
              },
              loading: () => const SliverToBoxAdapter(
                child: Padding(
                  padding: EdgeInsets.symmetric(vertical: AuraSpacing.xl),
                  child: Center(child: CircularProgressIndicator()),
                ),
              ),
              error: (err, _) => SliverToBoxAdapter(
                child: AuraFailureView(
                  failure: AuraFailure.from(err),
                  onRetry: () => ref.invalidate(engineerRunsProvider),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

void _showRunSheet(BuildContext context, EngineerRun run) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder: (context) => _EngineerRunDetailSheet(run: run),
  );
}

/// Icon and on-screen label for a tool name the engineer agent records; an unknown tool falls back
/// to a generic icon and its raw name.
(IconData, String) _toolDisplay(AppLocalizations l10n, String tool) =>
    switch (tool) {
      'github_create_draft_pull_request' => (
        Icons.merge_type,
        l10n.engineeringToolOpenedDraftPr,
      ),
      'github_commit_files' => (
        Icons.upload_file,
        l10n.engineeringToolCommittedFiles,
      ),
      'github_create_branch' => (
        Icons.call_split,
        l10n.engineeringToolCreatedBranch,
      ),
      'write_file' => (Icons.note_add_outlined, l10n.engineeringToolWroteFile),
      'edit_file' => (Icons.edit_note_outlined, l10n.engineeringToolEditedFile),
      'run_command' => (Icons.terminal, l10n.engineeringToolRanCommand),
      'git_commit' => (Icons.commit, l10n.engineeringToolCommitted),
      'git_checkout_branch' => (
        Icons.call_split,
        l10n.engineeringToolSwitchedBranch,
      ),
      'git_diff' => (
        Icons.difference_outlined,
        l10n.engineeringToolDiffedSandbox,
      ),
      _ => (Icons.build_outlined, tool),
    };

/// The run's status word as shown on screen; an unknown status is shown as the server sent it.
String _statusLabel(AppLocalizations l10n, String status) => switch (status) {
  'pending' => l10n.engineeringRunStatusPending,
  'succeeded' => l10n.engineeringRunStatusSucceeded,
  'failed' => l10n.engineeringRunStatusFailed,
  _ => status,
};

class _EngineerRunTile extends StatelessWidget {
  const _EngineerRunTile({
    required this.run,
    required this.onTap,
    this.selected = false,
  });

  final EngineerRun run;
  final VoidCallback onTap;
  final bool selected;

  bool get _failed => run.status == 'failed';

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final (icon, label) = _toolDisplay(context.l10n, run.tool);
    final statusColor = switch (run.status) {
      'succeeded' => palette.success,
      'failed' => palette.danger,
      _ => palette.warning,
    };
    final statusWord = _statusLabel(context.l10n, run.status);

    final body = InkWell(
      onTap: onTap,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 20, color: _failed ? palette.danger : palette.brand),
          const SizedBox(width: AuraSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  label,
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    fontWeight: FontWeight.w600,
                    color: _failed ? palette.danger : null,
                  ),
                ),
                const SizedBox(height: 2),
                if (run.prUrl != null)
                  Text(
                    run.prUrl!,
                    style: TextStyle(
                      fontFamily: 'Courier',
                      fontSize: 12,
                      color: palette.brand,
                    ),
                    overflow: TextOverflow.ellipsis,
                  )
                else if (run.branch != null)
                  Text(
                    run.branch!,
                    style: TextStyle(
                      fontFamily: 'Courier',
                      fontSize: 12,
                      color: palette.textSecondary,
                    ),
                  ),
                const SizedBox(height: 2),
                Text(
                  DateFormat.yMMMd().add_jm().format(run.createdAt),
                  style: TextStyle(fontSize: 11, color: palette.textSecondary),
                ),
              ],
            ),
          ),
          if (_failed)
            // Red is not the only signal: a failed run also says so in words, with its own icon.
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.error_outline, size: 16, color: palette.danger),
                const SizedBox(width: 4),
                Text(
                  statusWord,
                  key: const ValueKey('engineer-run-failed-label'),
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: palette.danger,
                  ),
                ),
              ],
            )
          else
            Container(
              width: 8,
              height: 8,
              margin: const EdgeInsets.only(top: 4),
              decoration: BoxDecoration(
                color: statusColor,
                shape: BoxShape.circle,
              ),
            ),
        ],
      ),
    );

    final card = _failed
        ? Container(
            key: const ValueKey('engineer-run-failed-card'),
            padding: const EdgeInsets.all(AuraSpacing.lg),
            decoration: BoxDecoration(
              color: palette.danger.withValues(alpha: 0.10),
              borderRadius: BorderRadius.circular(AuraRadius.lg),
              border: Border.all(color: palette.danger, width: 1),
            ),
            child: body,
          )
        : AuraCard(child: body);

    return Semantics(
      label: '$label, ${context.l10n.engineeringRunStatus(statusWord)}',
      selected: selected,
      child: DecoratedBox(
        position: DecorationPosition.foreground,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(AuraRadius.lg),
          border: selected ? Border.all(color: palette.brand, width: 2) : null,
        ),
        child: card,
      ),
    );
  }
}

class _EngineerRunDetailSheet extends StatelessWidget {
  const _EngineerRunDetailSheet({required this.run});

  final EngineerRun run;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final maxHeight = MediaQuery.sizeOf(context).height * 0.8;

    return SafeArea(
      child: Align(
        alignment: Alignment.bottomCenter,
        child: ConstrainedBox(
          constraints: BoxConstraints(maxHeight: maxHeight),
          child: Container(
            decoration: BoxDecoration(
              color: palette.surface,
              borderRadius: const BorderRadius.vertical(
                top: Radius.circular(AuraRadius.xl),
              ),
              border: Border.all(color: palette.border),
            ),
            padding: const EdgeInsets.all(AuraSpacing.lg),
            child: SingleChildScrollView(
              child: _EngineerRunDetailBody(run: run),
            ),
          ),
        ),
      ),
    );
  }
}

/// A run's status, branch, commit, link and output: the content of the bottom sheet on a phone
/// and of the detail pane beside the run list on a wide web window.
class _EngineerRunDetailBody extends StatelessWidget {
  const _EngineerRunDetailBody({required this.run});

  final EngineerRun run;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final (icon, label) = _toolDisplay(context.l10n, run.tool);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          children: [
            Icon(icon, color: palette.brand),
            const SizedBox(width: AuraSpacing.sm),
            Expanded(
              child: Text(label, style: Theme.of(context).textTheme.titleSmall),
            ),
          ],
        ),
        const SizedBox(height: AuraSpacing.sm),
        Text(
          context.l10n.engineeringRunStatus(
            _statusLabel(context.l10n, run.status),
          ),
          style: run.status == 'failed'
              ? TextStyle(color: palette.danger, fontWeight: FontWeight.w600)
              : TextStyle(color: palette.textSecondary),
        ),
        if (run.branch != null)
          Text(
            context.l10n.engineeringRunBranch(run.branch!),
            style: TextStyle(color: palette.textSecondary),
          ),
        if (run.commitSha != null)
          Text(
            context.l10n.engineeringRunCommit(run.commitSha!),
            style: TextStyle(color: palette.textSecondary),
          ),
        if (run.prUrl != null) ...[
          const SizedBox(height: AuraSpacing.sm),
          Row(
            children: [
              Expanded(
                child: Text(
                  run.prUrl!,
                  style: TextStyle(
                    fontFamily: 'Courier',
                    fontSize: 13,
                    color: palette.brand,
                  ),
                ),
              ),
              IconButton(
                tooltip: context.l10n.engineeringCopyLink,
                icon: Icon(Icons.copy, size: 18, color: palette.textSecondary),
                onPressed: () async {
                  await Clipboard.setData(ClipboardData(text: run.prUrl!));
                  if (context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(
                        content: Text(context.l10n.engineeringLinkCopied),
                      ),
                    );
                  }
                },
              ),
            ],
          ),
        ],
        if (run.sandboxTranscript != null &&
            run.sandboxTranscript!.trim().isNotEmpty) ...[
          const SizedBox(height: AuraSpacing.md),
          Text(
            context.l10n.engineeringRunOutput,
            style: Theme.of(
              context,
            ).textTheme.bodySmall?.copyWith(fontWeight: FontWeight.w600),
          ),
          if (run.tool == 'git_diff')
            SandboxDiffView(diffText: run.sandboxTranscript!)
          else
            SandboxTerminalView(body: run.sandboxTranscript!),
        ],
      ],
    );
  }
}
