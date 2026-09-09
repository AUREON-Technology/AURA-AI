import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/ai/engineer/workspaces_panel.dart';
import 'package:aura_client/core/ai/engineer/engineer_workspaces.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/presentation/widgets/sandbox_diff_view.dart';
import 'package:aura_client/features/chat/presentation/widgets/sandbox_terminal_view.dart';

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
class EngineerHistoryPage extends ConsumerWidget {
  const EngineerHistoryPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final runsAsync = ref.watch(engineerRunsProvider);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: palette.background,
        title: const Text('Engineer'),
        actions: [
          IconButton(
            tooltip: 'Desktop daemon',
            icon: const Icon(Icons.dns_outlined),
            onPressed: () => context.push('/engineer-daemon'),
          ),
        ],
      ),
      body: RefreshIndicator(
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
                    return const SliverToBoxAdapter(
                      child: AuraEmptyState(
                        icon: Icons.code_outlined,
                        title: 'No engineer runs yet',
                        message:
                            'Ask the Engineer agent to open a PR or run a command, and it will show up here.',
                      ),
                    );
                  }
                  return SliverList.separated(
                    itemCount: runs.length,
                    separatorBuilder: (_, _) =>
                        const SizedBox(height: AuraSpacing.sm),
                    itemBuilder: (context, index) =>
                        _EngineerRunTile(run: runs[index]),
                  );
                },
                loading: () => const SliverToBoxAdapter(
                  child: Padding(
                    padding: EdgeInsets.symmetric(vertical: AuraSpacing.xl),
                    child: Center(child: CircularProgressIndicator()),
                  ),
                ),
                error: (err, _) => SliverToBoxAdapter(
                  child: AuraEmptyState(
                    icon: Icons.error_outline,
                    title: 'Could not load history',
                    message: '$err',
                    actionLabel: 'Retry',
                    onAction: () => ref.invalidate(engineerRunsProvider),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

const Map<String, (IconData, String)> _toolDisplay = {
  'github_create_draft_pull_request': (Icons.merge_type, 'Opened draft PR'),
  'github_commit_files': (Icons.upload_file, 'Committed files'),
  'github_create_branch': (Icons.call_split, 'Created branch'),
  'write_file': (Icons.note_add_outlined, 'Wrote file'),
  'edit_file': (Icons.edit_note_outlined, 'Edited file'),
  'run_command': (Icons.terminal, 'Ran command'),
  'git_commit': (Icons.commit, 'Committed'),
  'git_checkout_branch': (Icons.call_split, 'Switched branch'),
  'git_diff': (Icons.difference_outlined, 'Diffed sandbox'),
};

class _EngineerRunTile extends StatelessWidget {
  const _EngineerRunTile({required this.run});

  final EngineerRun run;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final (icon, label) = _toolDisplay[run.tool] ?? (Icons.build_outlined, run.tool);
    final statusColor = switch (run.status) {
      'succeeded' => palette.success,
      'failed' => palette.danger,
      _ => palette.warning,
    };

    return AuraCard(
      child: InkWell(
        onTap: () => _showDetail(context, run),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, size: 20, color: palette.brand),
            const SizedBox(width: AuraSpacing.sm),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(label, style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        fontWeight: FontWeight.w600,
                      )),
                  const SizedBox(height: 2),
                  if (run.prUrl != null)
                    Text(
                      run.prUrl!,
                      style: TextStyle(fontFamily: 'Courier', fontSize: 12, color: palette.brand),
                      overflow: TextOverflow.ellipsis,
                    )
                  else if (run.branch != null)
                    Text(
                      run.branch!,
                      style: TextStyle(fontFamily: 'Courier', fontSize: 12, color: palette.textSecondary),
                    ),
                  const SizedBox(height: 2),
                  Text(
                    DateFormat.yMMMd().add_jm().format(run.createdAt),
                    style: TextStyle(fontSize: 11, color: palette.textSecondary),
                  ),
                ],
              ),
            ),
            Container(
              width: 8,
              height: 8,
              margin: const EdgeInsets.only(top: 4),
              decoration: BoxDecoration(color: statusColor, shape: BoxShape.circle),
            ),
          ],
        ),
      ),
    );
  }

  void _showDetail(BuildContext context, EngineerRun run) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) => _EngineerRunDetailSheet(run: run),
    );
  }
}

class _EngineerRunDetailSheet extends StatelessWidget {
  const _EngineerRunDetailSheet({required this.run});

  final EngineerRun run;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final (icon, label) = _toolDisplay[run.tool] ?? (Icons.build_outlined, run.tool);
    final maxHeight = MediaQuery.sizeOf(context).height * 0.8;

    return SafeArea(
      child: Align(
        alignment: Alignment.bottomCenter,
        child: ConstrainedBox(
          constraints: BoxConstraints(maxHeight: maxHeight),
          child: Container(
            decoration: BoxDecoration(
              color: palette.surface,
              borderRadius: const BorderRadius.vertical(top: Radius.circular(AuraRadius.xl)),
              border: Border.all(color: palette.border),
            ),
            padding: const EdgeInsets.all(AuraSpacing.lg),
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    children: [
                      Icon(icon, color: palette.brand),
                      const SizedBox(width: AuraSpacing.sm),
                      Expanded(child: Text(label, style: Theme.of(context).textTheme.titleSmall)),
                    ],
                  ),
                  const SizedBox(height: AuraSpacing.sm),
                  Text('Status: ${run.status}', style: TextStyle(color: palette.textSecondary)),
                  if (run.branch != null)
                    Text('Branch: ${run.branch}', style: TextStyle(color: palette.textSecondary)),
                  if (run.commitSha != null)
                    Text('Commit: ${run.commitSha}', style: TextStyle(color: palette.textSecondary)),
                  if (run.prUrl != null) ...[
                    const SizedBox(height: AuraSpacing.sm),
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            run.prUrl!,
                            style: TextStyle(fontFamily: 'Courier', fontSize: 13, color: palette.brand),
                          ),
                        ),
                        IconButton(
                          tooltip: 'Copy link',
                          icon: Icon(Icons.copy, size: 18, color: palette.textSecondary),
                          onPressed: () async {
                            await Clipboard.setData(ClipboardData(text: run.prUrl!));
                            if (context.mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(
                                const SnackBar(content: Text('Link copied')),
                              );
                            }
                          },
                        ),
                      ],
                    ),
                  ],
                  if (run.sandboxTranscript != null && run.sandboxTranscript!.trim().isNotEmpty) ...[
                    const SizedBox(height: AuraSpacing.md),
                    Text('Output', style: Theme.of(context).textTheme.bodySmall?.copyWith(
                          fontWeight: FontWeight.w600,
                        )),
                    if (run.tool == 'git_diff')
                      SandboxDiffView(diffText: run.sandboxTranscript!)
                    else
                      SandboxTerminalView(body: run.sandboxTranscript!),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
