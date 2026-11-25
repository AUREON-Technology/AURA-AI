import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';
import 'package:aura_client/l10n/l10n.dart';

import '../data/run_history_repository.dart';

String runStatusLabel(AppLocalizations l10n, String status) => switch (status) {
      'failed' => l10n.runStatusFailed,
      'completed' => l10n.runStatusCompleted,
      'partial' => l10n.runStatusPartial,
      'running' => l10n.runStatusRunning,
      'awaiting_approval' => l10n.runStatusAwaitingApproval,
      'budget_exhausted' => l10n.runStatusBudgetExhausted,
      _ => status,
    };

/// The query the page is showing ('' lists the newest runs).
class _RunSearchQuery extends Notifier<String> {
  @override
  String build() => '';

  void set(String value) => state = value;
}

final _runSearchQueryProvider = NotifierProvider.autoDispose<_RunSearchQuery, String>(_RunSearchQuery.new);

final _runSearchProvider = FutureProvider.autoDispose<RunSearchResult>((ref) {
  final query = ref.watch(_runSearchQueryProvider);
  return ref.read(runHistoryRepositoryProvider).search(query: query);
});

/// Search the person's agent runs and open any one as a timeline (ADR-043 section 5, inventory
/// row 71). Reached from the Engineer history page.
class RunHistoryPage extends ConsumerStatefulWidget {
  const RunHistoryPage({super.key});

  @override
  ConsumerState<RunHistoryPage> createState() => _RunHistoryPageState();
}

class _RunHistoryPageState extends ConsumerState<RunHistoryPage> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _search() => ref.read(_runSearchQueryProvider.notifier).set(_controller.text.trim());

  Future<void> _stopAll() async {
    final l10n = context.l10n;
    final messenger = ScaffoldMessenger.of(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(l10n.agentStopAllTitle),
        content: Text(l10n.agentStopAllMessage),
        actions: [
          TextButton(onPressed: () => Navigator.of(ctx).pop(false), child: Text(l10n.cancel)),
          FilledButton(
            key: const ValueKey('stop-all-confirm'),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: Text(l10n.agentStopAllConfirm),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      final result = await ref.read(runHistoryRepositoryProvider).stopAll();
      ref.invalidate(_runSearchProvider);
      messenger.showSnackBar(SnackBar(
        content: Text(result.stoppedAnything
            ? l10n.agentStopAllDone(result.turnsStopped, result.desktopSessionsClosed)
            : l10n.agentStopAllNothing),
      ));
    } catch (err) {
      messenger.showSnackBar(SnackBar(content: Text(AuraFailure.from(err).message)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final results = ref.watch(_runSearchProvider);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: palette.background,
        title: Text(l10n.runSearchTitle),
        actions: [
          IconButton(
            key: const ValueKey('stop-all-button'),
            tooltip: l10n.agentStopAllTitle,
            icon: Icon(Icons.stop_circle_outlined, color: Theme.of(context).colorScheme.error),
            onPressed: _stopAll,
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () => ref.refresh(_runSearchProvider.future),
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(AuraSpacing.md),
          children: [
            Row(
              children: [
                Expanded(
                  child: AuraTextField(
                    controller: _controller,
                    hintText: l10n.runSearchHint,
                    prefixIcon: Icons.search,
                    textInputAction: TextInputAction.search,
                    onSubmitted: (_) => _search(),
                  ),
                ),
                const SizedBox(width: AuraSpacing.sm),
                IconButton.filled(
                  key: const ValueKey('run-search-button'),
                  tooltip: l10n.runSearchButton,
                  icon: const Icon(Icons.search),
                  onPressed: _search,
                ),
              ],
            ),
            const SizedBox(height: AuraSpacing.md),
            results.when(
              data: (result) => _Results(result: result),
              loading: () => const Padding(
                padding: EdgeInsets.symmetric(vertical: AuraSpacing.xl),
                child: Center(child: CircularProgressIndicator()),
              ),
              error: (err, _) => AuraFailureView(
                failure: AuraFailure.from(err),
                onRetry: () => ref.invalidate(_runSearchProvider),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Results extends StatelessWidget {
  const _Results({required this.result});

  final RunSearchResult result;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    if (result.runs.isEmpty) {
      return AuraEmptyState(
        icon: Icons.search_off,
        title: l10n.runSearchEmpty,
        message: l10n.runSearchEmptyMessage,
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          l10n.runSearchResults(result.totalMatches),
          style: TextStyle(fontSize: 12, color: palette.textSecondary),
        ),
        // What is being searched, in the server's own words (goal text is not stored by default).
        if (result.scopeNote != null && result.scopeNote!.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 2, bottom: AuraSpacing.sm),
            child: Text(result.scopeNote!, style: TextStyle(fontSize: 11, color: palette.textSecondary)),
          ),
        for (final run in result.runs)
          Padding(
            padding: const EdgeInsets.only(bottom: AuraSpacing.sm),
            child: RunTile(run: run, onTap: () => showRunTimeline(context, run.id)),
          ),
      ],
    );
  }
}

/// One run in a list. A failed run is red *and* says so in words with its own icon.
class RunTile extends StatelessWidget {
  const RunTile({super.key, required this.run, this.onTap});

  final AgentRunSummary run;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final failed = run.isFailed;
    final word = runStatusLabel(l10n, run.status);
    final color = failed
        ? palette.danger
        : switch (run.status) {
            'completed' => palette.success,
            _ => palette.warning,
          };
    final body = InkWell(
      onTap: onTap,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(failed ? Icons.error_outline : Icons.smart_toy_outlined, size: 20, color: failed ? palette.danger : palette.brand),
          const SizedBox(width: AuraSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  run.goalPreview ?? run.agent,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                        fontWeight: FontWeight.w600,
                        color: failed ? palette.danger : null,
                      ),
                ),
                if (run.goalPreview != null)
                  Text(run.agent, style: TextStyle(fontSize: 12, color: palette.textSecondary)),
                const SizedBox(height: 2),
                Text(
                  l10n.runSummaryLine(run.planStepsTotal, run.toolCalls),
                  style: TextStyle(fontSize: 12, color: palette.textSecondary),
                ),
                if (run.tools.isNotEmpty)
                  Text(
                    run.tools.take(4).join(', '),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontFamily: 'Courier', fontSize: 11, color: palette.textSecondary),
                  ),
                Text(
                  DateFormat.yMMMd().add_jm().format(run.createdAt.toLocal()),
                  style: TextStyle(fontSize: 11, color: palette.textSecondary),
                ),
              ],
            ),
          ),
          Text(
            word,
            key: failed ? const ValueKey('run-failed-label') : null,
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: color),
          ),
        ],
      ),
    );
    final card = failed
        ? Container(
            key: const ValueKey('run-failed-card'),
            padding: const EdgeInsets.all(AuraSpacing.lg),
            decoration: BoxDecoration(
              color: palette.danger.withValues(alpha: 0.10),
              borderRadius: BorderRadius.circular(AuraRadius.lg),
              border: Border.all(color: palette.danger),
            ),
            child: body,
          )
        : AuraCard(child: body);
    return Semantics(label: '${run.agent}, $word', child: card);
  }
}

void showRunTimeline(BuildContext context, String runId) {
  showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    builder: (_) => RunTimelineSheet(runId: runId),
  );
}

/// The timeline of one run: its metrics and every recorded step in order, grouped by pass.
class RunTimelineSheet extends ConsumerWidget {
  const RunTimelineSheet({super.key, required this.runId});

  final String runId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final timeline = ref.watch(agentRunTimelineProvider(runId));
    final maxHeight = MediaQuery.sizeOf(context).height * 0.85;

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
                  Text(l10n.runTimelineTitle, style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: AuraSpacing.sm),
                  timeline.when(
                    data: (t) => _TimelineBody(timeline: t),
                    loading: () => const Padding(
                      padding: EdgeInsets.symmetric(vertical: AuraSpacing.xl),
                      child: Center(child: CircularProgressIndicator()),
                    ),
                    error: (err, _) => AuraFailureView(
                      failure: AuraFailure.from(err),
                      onRetry: () => ref.invalidate(agentRunTimelineProvider(runId)),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _TimelineBody extends StatelessWidget {
  const _TimelineBody({required this.timeline});

  final AgentRunTimeline timeline;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final run = timeline.run;
    final failed = run.isFailed;
    final word = runStatusLabel(l10n, run.status);
    final fmt = DateFormat.Hms();

    Widget metric(String label, String value) => Padding(
          padding: const EdgeInsetsDirectional.only(end: AuraSpacing.lg),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label, style: TextStyle(fontSize: 11, color: palette.textSecondary)),
              Text(value, style: const TextStyle(fontWeight: FontWeight.w600)),
            ],
          ),
        );

    final steps = timeline.steps;
    final children = <Widget>[
      Row(
        children: [
          Icon(failed ? Icons.error_outline : Icons.smart_toy_outlined, size: 18, color: failed ? palette.danger : palette.brand),
          const SizedBox(width: 6),
          Expanded(child: Text(run.goalPreview ?? run.agent, style: const TextStyle(fontWeight: FontWeight.w600))),
          Text(
            word,
            key: const ValueKey('run-timeline-status'),
            style: TextStyle(fontWeight: FontWeight.w600, color: failed ? palette.danger : palette.textSecondary),
          ),
        ],
      ),
      const SizedBox(height: AuraSpacing.sm),
      Wrap(
        runSpacing: AuraSpacing.sm,
        children: [
          metric(l10n.runMetricElapsed, l10n.runSeconds(run.elapsedSeconds.toStringAsFixed(1))),
          metric(l10n.runMetricTokens, '${run.tokensUsed}'),
          metric(l10n.runMetricToolCalls, '${run.toolCallsOk}/${run.toolCalls}'),
          metric(l10n.runMetricIterations, '${run.iterations}'),
        ],
      ),
      const SizedBox(height: AuraSpacing.md),
    ];

    if (steps.isEmpty) {
      children.add(Text(l10n.runTimelineNoSteps, style: TextStyle(color: palette.textSecondary)));
    }
    int? currentIteration;
    for (final step in steps) {
      if (step.iteration != currentIteration) {
        currentIteration = step.iteration;
        children.add(Padding(
          padding: const EdgeInsets.only(top: AuraSpacing.sm, bottom: AuraSpacing.xs),
          child: Text(
            l10n.runTimelineIteration(step.iteration),
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: palette.textSecondary),
          ),
        ));
      }
      final stepColor = step.isFailed ? palette.danger : palette.textPrimary;
      children.add(Padding(
        key: ValueKey('run-step-${step.seq}'),
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(step.isFailed ? Icons.error_outline : Icons.check_circle_outline, size: 16, color: step.isFailed ? palette.danger : palette.success),
            const SizedBox(width: AuraSpacing.sm),
            Expanded(
              child: Text(
                [step.tool ?? step.kind, if (step.phase.isNotEmpty && step.tool != null) step.phase].join(' · '),
                style: TextStyle(color: stepColor, fontWeight: step.isFailed ? FontWeight.w600 : FontWeight.w400),
              ),
            ),
            if (step.isFailed)
              Text(step.errorKind ?? l10n.runTimelineStepFailed, style: TextStyle(fontSize: 12, color: palette.danger)),
            const SizedBox(width: AuraSpacing.sm),
            Text(fmt.format(step.createdAt.toLocal()), style: TextStyle(fontSize: 11, color: palette.textSecondary)),
          ],
        ),
      ));
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: children);
  }
}
