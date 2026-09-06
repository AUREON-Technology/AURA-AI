import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/chat_view_model.dart';

import 'code_preview.dart';

/// Replaces the old, argument-blind `ConfirmationCard`: renders what a confirm-gated tool call
/// actually proposes (branch/files/PR title/body for GitHub tools), not just its name, so
/// approving a draft PR no longer means approving it blind. Falls back to a generic
/// name+arguments table for any tool this doesn't have specific rendering for, so the card never
/// breaks on an unrecognized one (including `github_create_branch`, which is confirm-gated today
/// but has no dedicated sub-card).
class EngineerConfirmationCard extends ConsumerWidget {
  const EngineerConfirmationCard({
    super.key,
    this.onConfirm,
    this.onCancel,
  });

  /// When set, used instead of [chatViewModelProvider.confirmTools].
  final VoidCallback? onConfirm;

  /// When set, used instead of clearing [pendingConfirmationProvider] alone.
  final VoidCallback? onCancel;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(pendingConfirmationProvider);
    if (pending.isEmpty) return const SizedBox.shrink();

    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AuraSpacing.xl,
        vertical: AuraSpacing.sm,
      ),
      child: AuraCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              pending.length > 1 ? 'Confirm actions' : 'Confirm action',
              style: Theme.of(context).textTheme.titleSmall,
            ),
            const SizedBox(height: AuraSpacing.sm),
            for (var i = 0; i < pending.length; i++) ...[
              if (i > 0) const SizedBox(height: AuraSpacing.md),
              _PendingCallSummary(call: pending[i]),
            ],
            const SizedBox(height: AuraSpacing.md),
            Row(
              children: [
                Expanded(
                  child: AuraButton(
                    label: 'Cancel',
                    variant: AuraButtonVariant.secondary,
                    onPressed: onCancel ??
                        () => ref.read(pendingConfirmationProvider.notifier).clear(),
                  ),
                ),
                const SizedBox(width: AuraSpacing.md),
                Expanded(
                  child: AuraButton(
                    label: 'Confirm',
                    onPressed: onConfirm ??
                        () => ref.read(chatViewModelProvider.notifier).confirmTools(),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// Dispatches to a per-tool sub-card by `call.tool`, falling back to a generic
/// name+arguments table for anything unrecognized.
class _PendingCallSummary extends StatelessWidget {
  const _PendingCallSummary({required this.call});

  final PendingToolCall call;

  @override
  Widget build(BuildContext context) {
    switch (call.tool) {
      case 'github_create_draft_pull_request':
        return _DraftPrSummary(call: call);
      case 'github_commit_files':
        return _CommitFilesSummary(call: call);
      default:
        return _GenericToolSummary(call: call);
    }
  }
}

class _SummaryShell extends StatelessWidget {
  const _SummaryShell({required this.icon, required this.title, required this.children});

  final IconData icon;
  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Container(
      padding: const EdgeInsets.all(AuraSpacing.sm),
      decoration: BoxDecoration(
        color: palette.surfaceVariant,
        borderRadius: BorderRadius.circular(AuraRadius.sm),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Icon(icon, size: 16, color: palette.brand),
              const SizedBox(width: AuraSpacing.xs),
              Expanded(
                child: Text(
                  title,
                  style: Theme.of(context).textTheme.bodySmall?.copyWith(
                        fontWeight: FontWeight.w600,
                        color: palette.textSecondary,
                      ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          ...children,
        ],
      ),
    );
  }
}

class _DraftPrSummary extends StatelessWidget {
  const _DraftPrSummary({required this.call});

  final PendingToolCall call;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final a = call.arguments;
    return _SummaryShell(
      icon: Icons.merge_type,
      title: 'Open draft pull request',
      children: [
        const SizedBox(height: AuraSpacing.xs),
        if (a['title'] != null)
          Text(
            '${a['title']}',
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
          ),
        if (a['branch'] != null && a['base'] != null)
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text(
              '${a['branch']} → ${a['base']}',
              style: TextStyle(fontFamily: 'Courier', fontSize: 12, color: palette.textSecondary),
            ),
          ),
        if (a['body'] != null && '${a['body']}'.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: AuraSpacing.xs),
            child: Text(
              '${a['body']}',
              style: Theme.of(context).textTheme.bodySmall,
              maxLines: 6,
              overflow: TextOverflow.ellipsis,
            ),
          ),
      ],
    );
  }
}

class _CommitFilesSummary extends StatelessWidget {
  const _CommitFilesSummary({required this.call});

  final PendingToolCall call;

  @override
  Widget build(BuildContext context) {
    final a = call.arguments;
    final files = a['files'];
    final fileEntries = files is Map ? files.entries.toList() : const [];
    return _SummaryShell(
      icon: Icons.upload_file,
      title: 'Commit ${fileEntries.length} file(s)'
          '${a['branch'] != null ? ' to ${a['branch']}' : ''}',
      children: [
        if (a['message'] != null)
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text('${a['message']}', style: Theme.of(context).textTheme.bodyMedium),
          ),
        for (final entry in fileEntries)
          CodePreview(path: entry.key.toString(), content: entry.value?.toString() ?? ''),
      ],
    );
  }
}

class _GenericToolSummary extends StatelessWidget {
  const _GenericToolSummary({required this.call});

  final PendingToolCall call;

  @override
  Widget build(BuildContext context) {
    return _SummaryShell(
      icon: Icons.build_outlined,
      title: call.tool,
      children: [
        for (final entry in call.arguments.entries)
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text(
              '${entry.key}: ${entry.value}',
              style: Theme.of(context).textTheme.bodySmall,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
          ),
      ],
    );
  }
}
