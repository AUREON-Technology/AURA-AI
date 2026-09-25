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
  final _branchController = TextEditingController();
  String? _refreshedForKey;
  bool _historyExpanded = false;

  @override
  void dispose() {
    _messageController.dispose();
    _branchController.dispose();
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

  Future<void> _checkout(GitBranch branch) async {
    if (branch.name == ref.read(gitNotifierProvider).branch) return;
    await ref.read(gitNotifierProvider.notifier).checkout(branch.checkoutName);
  }

  Future<void> _showCreateBranchDialog() async {
    _branchController.clear();
    final name = await AuraDialog.show<String>(
      context: context,
      title: 'Create branch',
      child: AuraTextField(controller: _branchController, hintText: 'Branch name'),
      confirmLabel: 'Create',
      cancelLabel: 'Cancel',
      onConfirm: () => Navigator.of(context).pop(_branchController.text.trim()),
    );
    if (name == null || name.isEmpty || !mounted) return;
    await ref.read(gitNotifierProvider.notifier).checkout(name, create: true);
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
                Expanded(child: _BranchSelector(git: git, busy: git.busy, onSelect: _checkout)),
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
            child: Row(
              children: [
                _SyncButton(
                  icon: Icons.cloud_download,
                  tooltip: 'Fetch from all remotes',
                  busy: git.busy,
                  onPressed: () => ref.read(gitNotifierProvider.notifier).fetch(),
                ),
                _SyncButton(
                  icon: Icons.arrow_downward,
                  tooltip: 'Pull (fast-forward only)',
                  busy: git.busy,
                  onPressed: () => ref.read(gitNotifierProvider.notifier).pull(),
                ),
                _SyncButton(
                  icon: Icons.arrow_upward,
                  tooltip: 'Push current branch to its upstream',
                  busy: git.busy,
                  onPressed: () => ref.read(gitNotifierProvider.notifier).push(),
                ),
                const Spacer(),
                TextButton.icon(
                  icon: const Icon(Icons.add, size: 16),
                  label: const Text('Branch'),
                  onPressed: git.busy ? null : _showCreateBranchDialog,
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
                      if (_historyExpanded) {
                        await ref.read(gitNotifierProvider.notifier).loadLog();
                      }
                    },
            ),
          ),
          ExpansionTile(
            title: Text(
              'History',
              style: TextStyle(color: palette.textSecondary, fontSize: 11, fontWeight: FontWeight.w600),
            ),
            tilePadding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md),
            initiallyExpanded: _historyExpanded,
            onExpansionChanged: (expanded) {
              setState(() => _historyExpanded = expanded);
              if (expanded && ref.read(gitNotifierProvider).commits.isEmpty) {
                ref.read(gitNotifierProvider.notifier).loadLog();
              }
            },
            children: [
              if (git.commits.isEmpty)
                Padding(
                  padding: const EdgeInsets.fromLTRB(AuraSpacing.md, 0, AuraSpacing.md, AuraSpacing.sm),
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: Text('No commits loaded.', style: TextStyle(color: palette.textDisabled, fontSize: 12)),
                  ),
                )
              else
                for (final commit in git.commits) _CommitRow(commit: commit, palette: palette),
            ],
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

/// Branch switcher: a dropdown once the branch list has loaded, plain text
/// before that (or on detached HEAD). Remote-tracking entries show a
/// `(remote)` marker; picking one checks out its short name so git
/// DWIM-creates the local tracking branch (see [GitBranch.checkoutName]).
class _BranchSelector extends StatelessWidget {
  const _BranchSelector({required this.git, required this.busy, required this.onSelect});

  final GitState git;
  final bool busy;
  final ValueChanged<GitBranch> onSelect;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    const nameStyle = TextStyle(fontWeight: FontWeight.w600, fontSize: 13);
    if (git.branches.isEmpty) {
      return Text(
        git.branch ?? '(detached HEAD)',
        overflow: TextOverflow.ellipsis,
        style: nameStyle.copyWith(color: palette.textPrimary),
      );
    }
    GitBranch? selected;
    for (final branch in git.branches) {
      if (branch.name == git.branch) selected = branch;
    }
    return DropdownButtonHideUnderline(
      child: DropdownButton<GitBranch>(
        value: selected,
        isExpanded: true,
        hint: Text('(detached HEAD)', style: nameStyle.copyWith(color: palette.textSecondary)),
        style: nameStyle.copyWith(color: palette.textPrimary),
        items: [
          for (final branch in git.branches)
            DropdownMenuItem(
              value: branch,
              child: Text(
                branch.isRemote ? '${branch.name} (remote)' : branch.name,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 13),
              ),
            ),
        ],
        onChanged: busy
            ? null
            : (branch) {
                if (branch != null) onSelect(branch);
              },
      ),
    );
  }
}

class _SyncButton extends StatelessWidget {
  const _SyncButton({required this.icon, required this.tooltip, required this.busy, required this.onPressed});

  final IconData icon;
  final String tooltip;
  final bool busy;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return IconButton(
      icon: Icon(icon, size: 18, color: palette.textSecondary),
      tooltip: tooltip,
      onPressed: busy ? null : onPressed,
    );
  }
}

class _CommitRow extends StatelessWidget {
  const _CommitRow({required this.commit, required this.palette});

  final GitCommit commit;
  final AuraPalette palette;

  @override
  Widget build(BuildContext context) {
    final sha = commit.sha.length >= 7 ? commit.sha.substring(0, 7) : commit.sha;
    final date = commit.date.length >= 10 ? commit.date.substring(0, 10) : commit.date;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md, vertical: AuraSpacing.xs),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            commit.subject,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(color: palette.textPrimary, fontSize: 13),
          ),
          Text(
            '$sha · ${commit.author} · $date',
            style: TextStyle(color: palette.textDisabled, fontSize: 11),
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
