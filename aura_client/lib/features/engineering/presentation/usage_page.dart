import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';
import 'package:aura_client/l10n/l10n.dart';

import '../data/usage_repository.dart';

String _money(AppLocalizations l10n, double usd) =>
    l10n.usageCostValue(usd >= 1 ? usd.toStringAsFixed(2) : usd.toStringAsFixed(4));

String _usagePeriodLabel(AppLocalizations l10n, String p) => switch (p) {
      'day' => l10n.usagePeriodDay,
      'week' => l10n.usagePeriodWeek,
      _ => l10n.usagePeriodMonth,
    };

String _usageGroupLabel(AppLocalizations l10n, String g) => switch (g) {
      'day' => l10n.usageGroupDay,
      'agent' => l10n.usageGroupAgent,
      _ => l10n.usageGroupTask,
    };

/// Tokens, cost and time over the trailing day, week or month, grouped by day, agent or task,
/// plus the most recent turns (ADR-043 section 6, inventory row 72). No chart dependency: the
/// breakdown is a list of proportional bars built from design-system tokens.
class UsagePage extends ConsumerStatefulWidget {
  const UsagePage({super.key});

  @override
  ConsumerState<UsagePage> createState() => _UsagePageState();
}

class _UsagePageState extends ConsumerState<UsagePage> {
  String _period = 'week';
  String _groupBy = 'day';

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final query = (period: _period, groupBy: _groupBy);
    final summary = ref.watch(usageSummaryProvider(query));
    final turns = ref.watch(usageTurnsProvider);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(backgroundColor: palette.background, title: Text(l10n.usageTitle)),
      body: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(usageTurnsProvider);
          ref.invalidate(usageSummaryProvider(query));
          await ref.read(usageSummaryProvider(query).future);
        },
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(AuraSpacing.md),
          children: [
            _Switcher(
              keyPrefix: 'usage-period',
              values: usagePeriods,
              selected: _period,
              label: (v) => _usagePeriodLabel(l10n, v),
              onSelected: (v) => setState(() => _period = v),
            ),
            const SizedBox(height: AuraSpacing.sm),
            _Switcher(
              keyPrefix: 'usage-group',
              values: usageGroupings,
              selected: _groupBy,
              label: (v) => _usageGroupLabel(l10n, v),
              onSelected: (v) => setState(() => _groupBy = v),
            ),
            const SizedBox(height: AuraSpacing.md),
            summary.when(
              data: (s) => _SummaryView(summary: s),
              loading: () => const Padding(
                padding: EdgeInsets.symmetric(vertical: AuraSpacing.xl),
                child: Center(child: CircularProgressIndicator()),
              ),
              error: (err, _) => AuraFailureView(
                failure: AuraFailure.from(err),
                onRetry: () => ref.invalidate(usageSummaryProvider(query)),
              ),
            ),
            const SizedBox(height: AuraSpacing.lg),
            AuraSectionHeader(title: l10n.usageRecentTurns),
            turns.when(
              data: (list) => list.isEmpty
                  ? Text(l10n.usageNoData, style: TextStyle(color: palette.textSecondary))
                  : Column(children: [for (final t in list) _TurnTile(turn: t)]),
              loading: () => const SizedBox.shrink(),
              error: (err, _) => AuraFailureView(
                failure: AuraFailure.from(err),
                compact: true,
                onRetry: () => ref.invalidate(usageTurnsProvider),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Switcher extends StatelessWidget {
  const _Switcher({
    required this.keyPrefix,
    required this.values,
    required this.selected,
    required this.label,
    required this.onSelected,
  });

  final String keyPrefix;
  final List<String> values;
  final String selected;
  final String Function(String) label;
  final ValueChanged<String> onSelected;

  @override
  Widget build(BuildContext context) {
    return SegmentedButton<String>(
      showSelectedIcon: false,
      segments: [
        for (final v in values)
          ButtonSegment<String>(
            value: v,
            label: Text(label(v), key: ValueKey('$keyPrefix-$v'), overflow: TextOverflow.ellipsis),
          ),
      ],
      selected: {selected},
      onSelectionChanged: (s) => onSelected(s.first),
    );
  }
}

class _SummaryView extends StatelessWidget {
  const _SummaryView({required this.summary});

  final UsageSummary summary;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final totals = summary.totals;
    if (!summary.enabled || totals == null) {
      return AuraEmptyState(icon: Icons.insights_outlined, title: l10n.usageDisabled, message: summary.note ?? '');
    }
    final fmt = NumberFormat.decimalPattern();
    final maxCost = summary.groups.fold<double>(0, (m, g) => g.costUsd > m ? g.costUsd : m);
    final maxTokens = summary.groups.fold<int>(0, (m, g) => g.totalTokens > m ? g.totalTokens : m);

    Widget stat(String label, String value, {Key? key}) => Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label, style: TextStyle(fontSize: 11, color: palette.textSecondary)),
              Text(value, key: key, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
            ],
          ),
        );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AuraCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  stat(l10n.usageTotalCost, _money(l10n, totals.costUsd), key: const ValueKey('usage-total-cost')),
                  stat(l10n.usageTotalTurns, fmt.format(totals.turns), key: const ValueKey('usage-total-turns')),
                ],
              ),
              const SizedBox(height: AuraSpacing.sm),
              Row(
                children: [
                  stat(l10n.usageTotalTokensIn, fmt.format(totals.tokensIn)),
                  stat(l10n.usageTotalTokensOut, fmt.format(totals.tokensOut)),
                ],
              ),
              if (totals.failedTurns > 0)
                Padding(
                  padding: const EdgeInsets.only(top: AuraSpacing.sm),
                  child: Row(
                    children: [
                      Icon(Icons.error_outline, size: 14, color: palette.danger),
                      const SizedBox(width: 4),
                      Text(
                        l10n.usageFailedTurns(totals.failedTurns),
                        style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: palette.danger),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
        if (summary.truncated)
          Padding(
            padding: const EdgeInsets.only(top: AuraSpacing.sm),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.info_outline, size: 14, color: palette.warning),
                const SizedBox(width: 4),
                Expanded(
                  child: Text(
                    l10n.usageTruncated,
                    key: const ValueKey('usage-truncated'),
                    style: TextStyle(fontSize: 12, color: palette.textSecondary),
                  ),
                ),
              ],
            ),
          ),
        const SizedBox(height: AuraSpacing.md),
        AuraSectionHeader(title: l10n.usageBreakdown),
        if (summary.groups.isEmpty)
          Text(l10n.usageNoData, style: TextStyle(color: palette.textSecondary))
        else
          for (final g in summary.groups)
            _GroupBar(
              group: g,
              groupBy: summary.groupBy,
              // Bars follow cost; when nothing cost money (a local model) they follow tokens.
              fraction: maxCost > 0
                  ? g.costUsd / maxCost
                  : (maxTokens > 0 ? g.totalTokens / maxTokens : 0),
            ),
      ],
    );
  }
}

class _GroupBar extends StatelessWidget {
  const _GroupBar({required this.group, required this.groupBy, required this.fraction});

  final UsageTotals group;
  final String groupBy;
  final double fraction;

  String _name(AppLocalizations l10n) {
    final key = group.key ?? '';
    if (groupBy == 'task' && key == '(none)') return l10n.usageNoTask;
    // A task id is a UUID; the first block is enough to tell tasks apart on a phone.
    if (groupBy == 'task' && key.length > 12) return key.substring(0, 8);
    return key;
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final fmt = NumberFormat.decimalPattern();
    final name = _name(l10n);
    return Semantics(
      label: '$name, ${_money(l10n, group.costUsd)}, ${fmt.format(group.totalTokens)}',
      child: Padding(
        key: ValueKey('usage-group-${group.key}'),
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: Text(name, overflow: TextOverflow.ellipsis)),
                Text(_money(l10n, group.costUsd), style: const TextStyle(fontWeight: FontWeight.w600)),
              ],
            ),
            const SizedBox(height: 3),
            ClipRRect(
              borderRadius: BorderRadius.circular(AuraRadius.sm),
              child: LinearProgressIndicator(
                value: fraction.clamp(0.0, 1.0),
                minHeight: 8,
                color: palette.brand,
                backgroundColor: palette.border,
              ),
            ),
            Text(
              l10n.usageTurnLine(group.totalTokens, group.elapsedSeconds.toStringAsFixed(1)),
              style: TextStyle(fontSize: 11, color: palette.textSecondary),
            ),
          ],
        ),
      ),
    );
  }
}

class _TurnTile extends StatelessWidget {
  const _TurnTile({required this.turn});

  final UsageTurn turn;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    final ok = turn.succeeded;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(ok ? Icons.check_circle_outline : Icons.error_outline, size: 16, color: ok ? palette.success : palette.danger),
          const SizedBox(width: AuraSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  [turn.agent, if (turn.model != null && turn.model!.isNotEmpty) turn.model!].join(' · '),
                  overflow: TextOverflow.ellipsis,
                ),
                Text(
                  l10n.usageTurnLine(
                    turn.promptTokens + turn.completionTokens,
                    (turn.elapsedMs / 1000).toStringAsFixed(1),
                  ),
                  style: TextStyle(fontSize: 11, color: palette.textSecondary),
                ),
              ],
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(_money(l10n, turn.costUsd), style: const TextStyle(fontWeight: FontWeight.w600)),
              if (!ok)
                Text(
                  l10n.usageTurnFailed,
                  key: const ValueKey('usage-turn-failed'),
                  style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: palette.danger),
                )
              else if (turn.cached)
                Text(l10n.usageTurnCached, style: TextStyle(fontSize: 11, color: palette.textSecondary)),
              if (turn.at != null)
                Text(
                  DateFormat.MMMd().add_jm().format(turn.at!.toLocal()),
                  style: TextStyle(fontSize: 11, color: palette.textSecondary),
                ),
            ],
          ),
        ],
      ),
    );
  }
}
