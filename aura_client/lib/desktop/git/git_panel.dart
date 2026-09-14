/// Source Control (Git) panel for Desktop IDE (D5).
///
/// Sidebar panel shown in the primary sidebar when `ActivityBar`'s "Source Control" item is
/// selected (`desktop_shell.dart`'s switch on `LayoutState.primarySidebarPanel`). Reads/writes
/// through `gitNotifierProvider`, which talks to `aura_hostd`'s `/git/*` routes
/// (`git_service.dart`) -- entirely separate from the agent-facing terminal/approval surface, per
/// D5's design decision 1 (a human's own click here is the consent).
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/git/git_diff_page.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

class GitPanel extends ConsumerStatefulWidget {
  const GitPanel({super.key});

  @override
  ConsumerState<GitPanel> createState() => _GitPanelState();
}

class _GitPanelState extends ConsumerState<GitPanel> {
  final _messageController = TextEditingController();
  String? _refreshedForKey;

  @override
  void dispose() {
    _messageController.dispose();
    super.dispose();
  }

  void _maybeRefresh(String workspaceId, String rootPath) {
    final key = '$workspaceId::$rootPath';
    if (_refreshedForKey == key) return;
    _refreshedForKey = key;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) ref.read(gitNotifierProvider.notifier).refresh(workspaceId, rootPath);
    });
  }

  void _openDiff(BuildContext context, String path, GitDiffScope scope) {
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => GitDiffPage(filePath: path, scope: scope),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final workspace = ref.watch(workspaceNotifierProvider).activeWorkspace;
    final hostdRoots = ref.watch(workspaceNotifierProvider).hostdRoots;

    if (workspace == null) {
      return _Centered(text: 'Open a workspace to use Source Control.', palette: palette);
    }
    final matchingRoot = hostdRoots.where((r) => r.path == workspace.rootPath).firstOrNull;
    if (matchingRoot == null) {
      return _Centered(text: 'Grant this workspace to the host daemon to use Source Control.', palette: palette);
    }

    _maybeRefresh(workspace.id, workspace.rootPath);
    final git = ref.watch(gitNotifierProvider);

    if (git.error != null) {
      return _Centered(
        text: git.error!,
        palette: palette,
        onRetry: () {
          _refreshedForKey = null;
          ref.read(gitNotifierProvider.notifier).refresh(workspace.id, workspace.rootPath);
        },
      );
    }

    final staged = git.files.where((f) => f.staged).toList();
    final unstaged = git.files.where((f) => !f.staged).toList();

    return Container(
      color: palette.surface,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.all(AuraSpacing.md),
            child: Row(
              children: [
                Icon(Icons.source_outlined, size: 16, color: palette.textSecondary),
                const SizedBox(width: AuraSpacing.xs),
                Expanded(
                  child: Text(
                    git.branch ?? '(detached HEAD)',
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(color: palette.textPrimary, fontWeight: FontWeight.w600),
                  ),
                ),
                if (git.ahead > 0 || git.behind > 0)
                  Text(
                    '${git.ahead > 0 ? '↑${git.ahead} ' : ''}${git.behind > 0 ? '↓${git.behind}' : ''}',
                    style: TextStyle(color: palette.textSecondary, fontSize: 12),
                  ),
                IconButton(
                  icon: const Icon(Icons.refresh, size: 18),
                  tooltip: 'Refresh',
                  onPressed: () => ref.read(gitNotifierProvider.notifier).refresh(workspace.id, workspace.rootPath),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md),
            child: AuraTextField(
              controller: _messageController,
              hintText: 'Commit message',
              maxLines: 2,
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(AuraSpacing.md),
            child: AuraButton(
              label: git.busy ? 'Working...' : 'Commit',
              expand: true,
              onPressed: staged.isEmpty || git.busy || _messageController.text.trim().isEmpty
                  ? null
                  : () async {
                      await ref.read(gitNotifierProvider.notifier).commit(_messageController.text.trim());
                      _messageController.clear();
                    },
            ),
          ),
          Expanded(
            child: ListView(
              children: [
                if (staged.isNotEmpty)
                  _Section(
                    title: 'Staged Changes',
                    palette: palette,
                    trailing: TextButton(
                      onPressed: git.busy
                          ? null
                          : () => ref.read(gitNotifierProvider.notifier).unstage(staged.map((f) => f.path).toList()),
                      child: const Text('Unstage All'),
                    ),
                    children: [
                      for (final file in staged)
                        _FileRow(
                          file: file,
                          palette: palette,
                          actionIcon: Icons.remove,
                          actionTooltip: 'Unstage',
                          onAction: git.busy ? null : () => ref.read(gitNotifierProvider.notifier).unstage([file.path]),
                          onTap: () => _openDiff(context, file.path, GitDiffScope.staged),
                        ),
                    ],
                  ),
                if (unstaged.isNotEmpty)
                  _Section(
                    title: 'Changes',
                    palette: palette,
                    trailing: TextButton(
                      onPressed: git.busy ? null : () => ref.read(gitNotifierProvider.notifier).stageAll(),
                      child: const Text('Stage All'),
                    ),
                    children: [
                      for (final file in unstaged)
                        _FileRow(
                          file: file,
                          palette: palette,
                          actionIcon: Icons.add,
                          actionTooltip: 'Stage',
                          onAction: git.busy ? null : () => ref.read(gitNotifierProvider.notifier).stage([file.path]),
                          onTap: () => _openDiff(context, file.path, GitDiffScope.workingTree),
                        ),
                    ],
                  ),
                if (staged.isEmpty && unstaged.isEmpty)
                  Padding(
                    padding: const EdgeInsets.all(AuraSpacing.lg),
                    child: Text('No changes.', style: TextStyle(color: palette.textSecondary)),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.title, required this.palette, required this.children, this.trailing});

  final String title;
  final AuraPalette palette;
  final List<Widget> children;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(AuraSpacing.md, AuraSpacing.sm, AuraSpacing.sm, 0),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  title.toUpperCase(),
                  style: TextStyle(color: palette.textSecondary, fontSize: 11, fontWeight: FontWeight.w600),
                ),
              ),
              ?trailing,
            ],
          ),
        ),
        ...children,
      ],
    );
  }
}

class _FileRow extends StatelessWidget {
  const _FileRow({
    required this.file,
    required this.palette,
    required this.actionIcon,
    required this.actionTooltip,
    required this.onAction,
    required this.onTap,
  });

  final GitFileChange file;
  final AuraPalette palette;
  final IconData actionIcon;
  final String actionTooltip;
  final VoidCallback? onAction;
  final VoidCallback onTap;

  Color _statusColor() {
    final status = file.staged ? file.indexStatus : file.worktreeStatus;
    return switch (status) {
      'A' => palette.success,
      'D' => palette.danger,
      'R' => palette.brand,
      '?' => palette.textSecondary,
      _ => palette.warning,
    };
  }

  @override
  Widget build(BuildContext context) {
    final status = file.staged ? file.indexStatus : file.worktreeStatus;
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md, vertical: AuraSpacing.xs),
        child: Row(
          children: [
            SizedBox(
              width: 16,
              child: Text(status, style: TextStyle(color: _statusColor(), fontWeight: FontWeight.w700, fontSize: 12)),
            ),
            const SizedBox(width: AuraSpacing.xs),
            Expanded(
              child: Text(
                file.path,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(color: palette.textPrimary, fontSize: 13),
              ),
            ),
            IconButton(
              icon: Icon(actionIcon, size: 16),
              tooltip: actionTooltip,
              onPressed: onAction,
              constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
              padding: EdgeInsets.zero,
            ),
          ],
        ),
      ),
    );
  }
}

class _Centered extends StatelessWidget {
  const _Centered({required this.text, required this.palette, this.onRetry});

  final String text;
  final AuraPalette palette;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: palette.surface,
      child: Center(
        child: Padding(
          padding: const EdgeInsets.all(AuraSpacing.lg),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(text, textAlign: TextAlign.center, style: TextStyle(color: palette.textSecondary)),
              if (onRetry != null) ...[
                const SizedBox(height: AuraSpacing.sm),
                TextButton(onPressed: onRetry, child: const Text('Retry')),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
