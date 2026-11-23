import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/l10n/l10n.dart';

import '../data/agent_task_outbox.dart';
import '../data/agent_task_repository.dart';

/// Send a task to the agent task gateway and watch it (ADR-041, inventory row 91).
///
/// A task that cannot be sent yet (offline, server down) is not lost: it is saved on this device
/// as "waiting to send" and replayed in order under the same `client_request_id`, so the server
/// can never run it twice. A task the server refuses for good stays on the list as "not sent",
/// with the reason, until the person dismisses it.
class AgentTasksPage extends ConsumerStatefulWidget {
  const AgentTasksPage({super.key});

  @override
  ConsumerState<AgentTasksPage> createState() => _AgentTasksPageState();
}

class _AgentTasksPageState extends ConsumerState<AgentTasksPage> {
  final _goal = TextEditingController();
  bool _submitting = false;

  @override
  void dispose() {
    _goal.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final l10n = context.l10n;
    final text = _goal.text.trim();
    if (text.isEmpty || _submitting) return;
    setState(() => _submitting = true);
    final outbox = ref.read(agentTaskOutboxProvider.notifier);
    final entry = await outbox.enqueue(text);
    if (!mounted) return;
    setState(() => _submitting = false);
    final messenger = ScaffoldMessenger.of(context);
    if (entry == null) {
      messenger.showSnackBar(SnackBar(content: Text(l10n.agentTasksQueueFull)));
      return;
    }
    _goal.clear();
    final state = ref.read(agentTaskOutboxProvider);
    final left = state.entries.where((e) => e.clientRequestId == entry.clientRequestId).firstOrNull;
    final message = left == null
        ? (state.lastAccepted?.idempotentReplay == true ? l10n.agentTasksAlreadySentSnack : l10n.agentTasksSentSnack)
        : (left.isFailed ? l10n.agentTasksNotSentSnack : l10n.agentTasksQueuedOffline);
    messenger.showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final owner = ref.watch(authProvider.select((a) => a.value?.id)) ?? '';
    final outbox = ref.watch(agentTaskOutboxProvider);
    // A newly accepted submission shows up in the server's list without a manual refresh.
    ref.listen(agentTaskOutboxProvider.select((s) => s.acceptedCount), (_, _) {
      ref.invalidate(agentTasksProvider);
    });
    final queued = outbox.forOwner(owner);
    final tasksAsync = ref.watch(agentTasksProvider);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(backgroundColor: palette.background, title: Text(l10n.agentTasksTitle)),
      body: RefreshIndicator(
        onRefresh: () async {
          await ref.read(agentTaskOutboxProvider.notifier).flush(force: true);
          ref.invalidate(agentTasksProvider);
        },
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(AuraSpacing.md),
          children: [
            AuraTextField(
              controller: _goal,
              hintText: l10n.agentTasksGoalHint,
              maxLines: 4,
            ),
            const SizedBox(height: AuraSpacing.sm),
            AuraButton(
              label: l10n.agentTasksSend,
              icon: Icons.send_outlined,
              loading: _submitting,
              onPressed: _send,
            ),
            for (final entry in queued) ...[
              const SizedBox(height: AuraSpacing.sm),
              _QueuedTaskTile(entry: entry, sending: outbox.sendingId == entry.clientRequestId),
            ],
            const SizedBox(height: AuraSpacing.lg),
            AuraSectionHeader(title: l10n.agentTasksSentTitle),
            tasksAsync.when(
              data: (tasks) => tasks.isEmpty
                  ? AuraEmptyState(
                      icon: Icons.task_alt_outlined,
                      title: l10n.agentTasksNoneYet,
                      message: l10n.agentTasksNoneYetMessage,
                    )
                  : Column(
                      children: [
                        for (final t in tasks)
                          Padding(
                            padding: const EdgeInsets.only(bottom: AuraSpacing.sm),
                            child: _TaskTile(task: t),
                          ),
                      ],
                    ),
              loading: () => const Padding(
                padding: EdgeInsets.symmetric(vertical: AuraSpacing.xl),
                child: Center(child: CircularProgressIndicator()),
              ),
              // Offline is expected here: the waiting list above still shows what is queued.
              error: (err, _) => AuraFailureView(
                failure: AuraFailure.from(err),
                compact: true,
                onRetry: () => ref.invalidate(agentTasksProvider),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _QueuedTaskTile extends ConsumerWidget {
  const _QueuedTaskTile({required this.entry, required this.sending});

  final QueuedAgentTask entry;
  final bool sending;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final failed = entry.isFailed;
    final accent = failed ? palette.danger : palette.warning;
    final label = failed ? l10n.agentTasksNotSent : (sending ? l10n.agentTasksSending : l10n.agentTasksWaiting);
    final reason = failed
        ? (entry.errorKind == 'idempotency_key_reused' ? l10n.agentTasksConflict : entry.lastError)
        : (entry.attempts > 0 ? l10n.agentTasksRetryingIn(entry.attempts) : null);
    final outbox = ref.read(agentTaskOutboxProvider.notifier);

    final content = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            // Colour is not the only signal: an icon and the words say the same thing.
            Icon(failed ? Icons.error_outline : Icons.schedule, size: 16, color: accent),
            const SizedBox(width: 4),
            Text(
              label,
              key: ValueKey(failed ? 'agent-task-not-sent-label' : 'agent-task-waiting-label'),
              style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: accent),
            ),
          ],
        ),
        const SizedBox(height: AuraSpacing.xs),
        Text(entry.goal, maxLines: 4, overflow: TextOverflow.ellipsis),
        if (reason != null && reason.isNotEmpty) ...[
          const SizedBox(height: AuraSpacing.xs),
          Text(reason, style: TextStyle(fontSize: 12, color: palette.textSecondary)),
        ],
        Wrap(
          spacing: AuraSpacing.sm,
          children: [
            if (!failed)
              TextButton(
                onPressed: sending ? null : () => outbox.flush(force: true),
                child: Text(l10n.agentTasksRetryNow),
              ),
            if (failed)
              TextButton(
                onPressed: () => outbox.resendAsNew(entry.clientRequestId),
                child: Text(l10n.agentTasksSendAgain),
              ),
            TextButton(
              onPressed: () => outbox.dismiss(entry.clientRequestId),
              child: Text(l10n.agentTasksDismiss),
            ),
          ],
        ),
      ],
    );

    return Semantics(
      label: '$label: ${entry.goal}',
      child: Container(
        key: ValueKey('agent-task-queued-${entry.clientRequestId}'),
        padding: const EdgeInsets.all(AuraSpacing.md),
        decoration: BoxDecoration(
          color: accent.withValues(alpha: 0.10),
          borderRadius: BorderRadius.circular(AuraRadius.lg),
          border: Border.all(color: accent),
        ),
        child: content,
      ),
    );
  }
}

String agentTaskStatusLabel(AppLocalizations l10n, String status) => switch (status) {
      'queued' => l10n.agentTaskStatusQueued,
      'running' => l10n.agentTaskStatusRunning,
      'awaiting_approval' => l10n.agentTaskStatusAwaitingApproval,
      'succeeded' => l10n.agentTaskStatusSucceeded,
      'failed' => l10n.agentTaskStatusFailed,
      'cancelled' => l10n.agentTaskStatusCancelled,
      _ => status,
    };

class _TaskTile extends StatelessWidget {
  const _TaskTile({required this.task});

  final AgentTask task;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final failed = task.isFailed;
    final color = failed
        ? palette.danger
        : switch (task.status) {
            'succeeded' => palette.success,
            'awaiting_approval' => palette.warning,
            _ => palette.textSecondary,
          };
    final word = agentTaskStatusLabel(l10n, task.status);
    return Semantics(
      label: '$word: ${task.goal}',
      child: AuraCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(failed ? Icons.error_outline : Icons.circle, size: failed ? 16 : 8, color: color),
                const SizedBox(width: 6),
                Text(
                  word,
                  key: failed ? const ValueKey('agent-task-failed-label') : null,
                  style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: color),
                ),
                const Spacer(),
                Text(
                  DateFormat.yMMMd().add_jm().format(task.createdAt.toLocal()),
                  style: TextStyle(fontSize: 11, color: palette.textSecondary),
                ),
              ],
            ),
            const SizedBox(height: AuraSpacing.xs),
            Text(task.goal, maxLines: 3, overflow: TextOverflow.ellipsis),
            if (failed && task.errorMessage != null)
              Padding(
                padding: const EdgeInsets.only(top: AuraSpacing.xs),
                child: Text(task.errorMessage!, style: TextStyle(fontSize: 12, color: palette.danger)),
              ),
          ],
        ),
      ),
    );
  }
}
