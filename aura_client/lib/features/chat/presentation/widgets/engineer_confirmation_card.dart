import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/data/turn_meta.dart';
import 'package:aura_client/features/chat/presentation/chat_view_model.dart';

import 'code_preview.dart';
import 'github_branch_summary.dart';
import 'sandbox_diff_view.dart';
import 'sandbox_file_view.dart';
import 'sandbox_terminal_view.dart';

/// Renders what a confirm-gated tool call actually proposes (branch/files/PR title/body for
/// GitHub tools, real file/command/diff views for sandbox tools), not just its name, so
/// approving a draft PR or a shell command no longer means approving it blind.
///
/// Scoped to one turn (per-message): when [turnId] is set, only pending calls whose
/// [PendingToolCall.turnId] matches are shown here -- the single global card this replaced had
/// no way to say which assistant bubble it belonged to. Pass `turnId: null` to show every chat
/// pending call regardless of turn, or [calls] to show a list the caller owns (live voice keeps
/// its own, separate from chat's).
///
/// Confirming sends the selected calls' server-issued approval ids, bound to this card's turn
/// (F-04). A call without an approval id cannot be confirmed, and Confirm is disabled while
/// another chat reply is still streaming -- a send then would be dropped, losing the approval.
class EngineerConfirmationCard extends ConsumerStatefulWidget {
  const EngineerConfirmationCard({
    super.key,
    this.turnId,
    this.calls,
    this.onConfirm,
    this.onCancel,
  });

  final String? turnId;

  /// When set, the calls to show, instead of reading [pendingConfirmationProvider].
  final List<PendingToolCall>? calls;

  /// When set, used instead of [chatViewModelProvider.confirmTools] and receives every pending
  /// call shown by this card (selection is not exposed to this callback -- callers using it,
  /// e.g. live voice, don't yet support per-call selection there).
  final VoidCallback? onConfirm;

  /// When set, used instead of [chatViewModelProvider.rejectTools].
  final VoidCallback? onCancel;

  @override
  ConsumerState<EngineerConfirmationCard> createState() => _EngineerConfirmationCardState();
}

class _EngineerConfirmationCardState extends ConsumerState<EngineerConfirmationCard> {
  final Set<String> _deselected = {};

  static String? _key(PendingToolCall call) => call.approvalId ?? call.callId;

  @override
  Widget build(BuildContext context) {
    final List<PendingToolCall> all = widget.calls ?? ref.watch(pendingConfirmationProvider);
    final List<PendingToolCall> pending = widget.turnId == null
        ? all
        : all.where((c) => c.turnId == widget.turnId).toList();
    if (pending.isEmpty) return const SizedBox.shrink();

    final ownsConfirm = widget.onConfirm != null;
    final streaming = !ownsConfirm && ref.watch(streamingMessageIdProvider) != null;
    // Only a call the server issued an approval id for can be approved from here; a caller with
    // its own confirm path (live voice) approves server-side state and needs none.
    final confirmable = ownsConfirm ? pending : pending.where((c) => c.canConfirm).toList();
    final selectable = confirmable.where((c) => _key(c) != null).length;
    final toConfirm = confirmable
        .where((c) => _key(c) == null || !_deselected.contains(_key(c)))
        .toList();
    final canPress = toConfirm.isNotEmpty && !streaming;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AuraSpacing.sm),
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
              _PendingCallRow(
                call: pending[i],
                selected: toConfirm.contains(pending[i]),
                selectable: selectable > 1 && confirmable.contains(pending[i]) &&
                    _key(pending[i]) != null,
                onToggle: (value) => setState(() {
                  final id = _key(pending[i]);
                  if (id == null) return;
                  if (value) {
                    _deselected.remove(id);
                  } else {
                    _deselected.add(id);
                  }
                }),
              ),
            ],
            const SizedBox(height: AuraSpacing.md),
            Row(
              children: [
                Expanded(
                  child: AuraButton(
                    label: 'Cancel',
                    variant: AuraButtonVariant.secondary,
                    onPressed: widget.onCancel ??
                        () => ref.read(chatViewModelProvider.notifier).rejectTools(pending),
                  ),
                ),
                const SizedBox(width: AuraSpacing.md),
                Expanded(
                  child: AuraButton(
                    label: toConfirm.length < pending.length ? 'Confirm selected' : 'Confirm',
                    onPressed: !canPress
                        ? null
                        : (widget.onConfirm ??
                            () => ref.read(chatViewModelProvider.notifier).confirmTools(toConfirm)),
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

class _PendingCallRow extends StatelessWidget {
  const _PendingCallRow({
    required this.call,
    required this.selected,
    required this.selectable,
    required this.onToggle,
  });

  final PendingToolCall call;
  final bool selected;
  final bool selectable;
  final ValueChanged<bool> onToggle;

  @override
  Widget build(BuildContext context) {
    final summary = _PendingCallSummary(call: call);
    if (!selectable) return summary;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Checkbox(value: selected, onChanged: (v) => onToggle(v ?? true)),
        Expanded(child: summary),
      ],
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
      case 'github_create_branch':
        return _SummaryShell(
          icon: Icons.call_split,
          title: 'Create branch',
          children: [GithubBranchSummary(arguments: call.arguments)],
        );
      case 'write_file':
        return _WriteFileSummary(call: call);
      case 'edit_file':
        return _EditFileSummary(call: call);
      case 'run_command':
        return _RunCommandSummary(call: call);
      case 'git_commit':
        return _GitCommitSummary(call: call);
      case 'git_checkout_branch':
        return _SummaryShell(
          icon: Icons.call_split,
          title: 'Switch to branch',
          children: [
            Text(
              '${call.arguments['branch'] ?? '?'}',
              style: const TextStyle(fontFamily: 'Courier', fontSize: 13),
            ),
          ],
        );
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
        // A real diff when the backend could produce one (an active sandbox session at
        // confirmation time); otherwise fall back to the old add-only content preview per file.
        if (call.diffPreview != null && call.diffPreview!.trim().isNotEmpty)
          SandboxDiffView(diffText: call.diffPreview!)
        else
          for (final entry in fileEntries)
            CodePreview(path: entry.key.toString(), content: entry.value?.toString() ?? ''),
      ],
    );
  }
}

class _WriteFileSummary extends StatelessWidget {
  const _WriteFileSummary({required this.call});

  final PendingToolCall call;

  @override
  Widget build(BuildContext context) {
    final a = call.arguments;
    return _SummaryShell(
      icon: Icons.note_add_outlined,
      title: 'Write file',
      children: [
        SandboxFileView(
          path: a['path']?.toString() ?? '?',
          content: a['content']?.toString() ?? '',
        ),
      ],
    );
  }
}

class _EditFileSummary extends StatelessWidget {
  const _EditFileSummary({required this.call});

  final PendingToolCall call;

  @override
  Widget build(BuildContext context) {
    final a = call.arguments;
    return _SummaryShell(
      icon: Icons.edit_note_outlined,
      title: 'Edit ${a['path'] ?? 'file'}',
      children: [
        SandboxDiffView.fromBeforeAfter(
          before: a['find']?.toString() ?? '',
          after: a['replace']?.toString() ?? '',
        ),
      ],
    );
  }
}

class _RunCommandSummary extends StatelessWidget {
  const _RunCommandSummary({required this.call});

  final PendingToolCall call;

  @override
  Widget build(BuildContext context) {
    final command = call.arguments['command']?.toString() ?? '';
    return _SummaryShell(
      icon: Icons.terminal,
      title: 'Run command in sandbox',
      children: [SandboxTerminalView(body: command)],
    );
  }
}

class _GitCommitSummary extends StatelessWidget {
  const _GitCommitSummary({required this.call});

  final PendingToolCall call;

  @override
  Widget build(BuildContext context) {
    final message = call.arguments['message']?.toString() ?? '';
    return _SummaryShell(
      icon: Icons.commit,
      title: 'Commit sandbox changes',
      children: [
        if (message.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text(message, style: Theme.of(context).textTheme.bodyMedium),
          ),
        if (call.diffPreview != null && call.diffPreview!.trim().isNotEmpty)
          SandboxDiffView(diffText: call.diffPreview!),
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
