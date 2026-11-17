import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/insights/insights_client.dart';
import '../../../l10n/generated/app_localizations.dart';
import '../../notifications/notification_center.dart';
import 'package:aura_client/core/ai/ai_router/aura_ai_router.dart';
import '../../tasks/data/task_repository.dart';
import '../../calendar/data/calendar_repository.dart';
import '../../finance/data/finance_repository.dart';
import '../../health/data/health_repository.dart';

/// V2 flagship screen — "Today". Replaces the V1 dashboard-of-shortcuts
/// (a grid of 4 unrelated domain cards) with the IA described in
/// Doc/V2_BLUEPRINT.md §3: a single AI-curated view answering "what does
/// AURA think I should know right now", with a persistent capture bar
/// (Copilot is one tap away, not buried at nav position 6 as in V1) and a
/// unified "due today" list merging tasks + calendar instead of two
/// separate siloed cards — the same cross-domain unification the future
/// Life Hub screen (Phase 1/2) will generalize.
///
/// Locally scoped to [AuraThemeV2] via the [Theme] wrapper below rather
/// than flipping the app-wide theme, since ~20 other V1 screens still read
/// hardcoded colors directly and aren't migrated yet (see blueprint §13
/// Phase 2). This keeps the new design language real and testable without
/// risking a global visual regression.
class HomePage extends ConsumerWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return const _TodayScaffold();
  }
}

class _TodayScaffold extends ConsumerWidget {
  const _TodayScaffold();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final tasksAsync = ref.watch(tasksStreamProvider);
    final calendarAsync = ref.watch(calendarEventsStreamProvider);
    final financeAsync = ref.watch(financeTransactionsStreamProvider);
    final healthAsync = ref.watch(dailyHealthStreamProvider(DateTime.now()));
    final aiStatus = ref.watch(aiRouterStatusProvider);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text(
          _greeting(context),
          style: Theme.of(context).textTheme.headlineMedium,
        ),
        actions: [
          const NotificationBellIcon(),
          IconButton(
            tooltip: AppLocalizations.of(context)!.settingsTooltip,
            icon: Icon(Icons.settings_outlined, color: palette.textSecondary),
            onPressed: () => context.go('/you'),
          ),
          const SizedBox(width: AuraSpacing.sm),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(tasksStreamProvider);
          ref.invalidate(calendarEventsStreamProvider);
          ref.invalidate(financeTransactionsStreamProvider);
          ref.invalidate(dailyHealthStreamProvider(DateTime.now()));
          ref.invalidate(insightsProvider);
          await ref.read(aiRouter).probeGateway();
        },
        child: ListView(
          padding: const EdgeInsets.all(AuraSpacing.lg),
          children: [
            Text(
              DateFormat('EEEE, MMMM d').format(DateTime.now()).toUpperCase(),
              style: Theme.of(context).textTheme.labelMedium?.copyWith(
                color: palette.brandOnSurfaceText,
                fontWeight: FontWeight.w700,
                letterSpacing: 1.2,
              ),
            ),
            const SizedBox(height: AuraSpacing.xl),
            _CaptureBar(),
            const SizedBox(height: AuraSpacing.xl),
            const _InsightsSection(),
            _DueTodayCard(tasksAsync: tasksAsync, calendarAsync: calendarAsync),
            const SizedBox(height: AuraSpacing.lg),
            _QuickStatsRow(
              tasksAsync: tasksAsync,
              financeAsync: financeAsync,
              healthAsync: healthAsync,
            ),
            const SizedBox(height: AuraSpacing.lg),
            _SystemHealthCard(aiStatus: aiStatus),
            const SizedBox(height: AuraSpacing.xxl),
          ],
        ),
      ),
    );
  }

  String _greeting(BuildContext context) {
    final l10n = AppLocalizations.of(context)!;
    final hour = DateTime.now().hour;
    if (hour < 12) return l10n.greetingMorning;
    if (hour < 18) return l10n.greetingAfternoon;
    return l10n.greetingEvening;
  }
}

/// The always-available AI entry point — IA priority #2 (Copilot), surfaced
/// here instead of requiring a nav-bar hop, per blueprint §3/§1.
class _CaptureBar extends ConsumerStatefulWidget {
  @override
  ConsumerState<_CaptureBar> createState() => _CaptureBarState();
}

class _CaptureBarState extends ConsumerState<_CaptureBar> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    final query = _controller.text.trim();
    if (query.isEmpty) return;
    context.go('/chat', extra: query);
    _controller.clear();
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return AuraCard(
      glass: true,
      semanticLabel: 'Ask AURA anything',
      child: Row(
        children: [
          Icon(Icons.auto_awesome, color: palette.brandOnSurfaceText, size: 20),
          const SizedBox(width: AuraSpacing.md),
          Expanded(
            child: TextField(
              controller: _controller,
              onSubmitted: (_) => _submit(),
              style: Theme.of(context).textTheme.bodyLarge,
              decoration: InputDecoration(
                border: InputBorder.none,
                hintText: AppLocalizations.of(context)!.captureBarHint,
                isDense: true,
              ),
            ),
          ),
          const SizedBox(width: AuraSpacing.sm),
          Semantics(
            button: true,
            label: 'Send to AURA',
            child: InkWell(
              borderRadius: BorderRadius.circular(AuraRadius.pill),
              onTap: _submit,
              child: Container(
                width: 40,
                height: 40,
                decoration: BoxDecoration(
                  gradient: palette.brandGradient,
                  shape: BoxShape.circle,
                  boxShadow: [
                    BoxShadow(
                      color: palette.brand.withValues(alpha: 0.35),
                      blurRadius: 12,
                      offset: const Offset(0, 4),
                    ),
                  ],
                ),
                child: Icon(
                  Icons.arrow_upward_rounded,
                  color: palette.onBrand,
                  size: 20,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Proactive AI Insights (Doc/V2_BLUEPRINT.md §8) — the first feature that
/// actually needs the backend to own user data (§6's Postgres migration).
/// Renders nothing at all (not even a skeleton/empty state) when there are
/// none, since an empty "Insights" section isn't informative the way an
/// empty "Due today" list is — it degrades silently on deployments that
/// haven't configured Postgres yet.
class _InsightsSection extends ConsumerWidget {
  const _InsightsSection();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final insightsAsync = ref.watch(insightsProvider);

    return insightsAsync.when(
      data: (insights) {
        if (insights.isEmpty) return const SizedBox.shrink();
        return Padding(
          padding: const EdgeInsets.only(bottom: AuraSpacing.lg),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (final insight in insights) ...[
                _InsightCard(insight: insight),
                const SizedBox(height: AuraSpacing.sm),
              ],
            ],
          ),
        );
      },
      loading: () => Padding(
        padding: const EdgeInsets.only(bottom: AuraSpacing.lg),
        child: AuraSkeleton(height: 72, width: double.infinity),
      ),
      error: (_, _) => Padding(
        padding: const EdgeInsets.only(bottom: AuraSpacing.lg),
        child: AuraEmptyState(
          icon: Icons.cloud_off_outlined,
          title: 'Could not load insights',
          message: 'Pull to refresh when you are back online.',
        ),
      ),
    );
  }
}

class _InsightCard extends ConsumerWidget {
  final AppInsight insight;
  const _InsightCard({required this.insight});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    return AuraCard(
      glass: true,
      padding: const EdgeInsets.all(AuraSpacing.md),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            Icons.tips_and_updates_outlined,
            size: 18,
            color: palette.warning,
          ),
          const SizedBox(width: AuraSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  insight.message,
                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                    color: palette.textPrimary,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  insight.explanation,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
          Semantics(
            button: true,
            label: 'Dismiss insight',
            child: InkWell(
              onTap: () async {
                await ref.read(insightsClientProvider).dismiss(insight.id);
                ref.invalidate(insightsProvider);
              },
              borderRadius: BorderRadius.circular(AuraRadius.pill),
              child: Padding(
                padding: const EdgeInsets.all(AuraSpacing.xs),
                child: Icon(Icons.close, size: 16, color: palette.textDisabled),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

enum _DueItemKind { task, event }

class _DueItem {
  final _DueItemKind kind;
  final String title;
  final DateTime? time;
  final bool overdue;
  const _DueItem({
    required this.kind,
    required this.title,
    this.time,
    this.overdue = false,
  });
}

/// Unified "what's due" list — merges tasks + calendar events into one
/// chronological view instead of two separate cards (V1 had
/// `_buildTasksOverviewCard` and `_buildEventsOverviewCard` as unrelated
/// siblings). This is the Today-screen expression of the Life Hub
/// unification principle in blueprint §3.
class _DueTodayCard extends StatelessWidget {
  final AsyncValue<List<dynamic>> tasksAsync;
  final AsyncValue<List<dynamic>> calendarAsync;
  const _DueTodayCard({required this.tasksAsync, required this.calendarAsync});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final loading = tasksAsync.isLoading || calendarAsync.isLoading;
    final hasError = tasksAsync.hasError || calendarAsync.hasError;

    final l10n = AppLocalizations.of(context)!;
    return AuraCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AuraSectionHeader(
            title: l10n.dueToday,
            actionLabel: l10n.viewAll,
            onAction: () => context.go('/life?type=tasks'),
          ),
          const SizedBox(height: AuraSpacing.md),
          if (loading)
            const Column(
              children: [
                AuraSkeleton(height: 18),
                SizedBox(height: 10),
                AuraSkeleton(height: 18),
                SizedBox(height: 10),
                AuraSkeleton(height: 18),
              ],
            )
          else if (hasError)
            AuraEmptyState(
              icon: Icons.error_outline,
              title: l10n.couldNotLoadDueToday,
            )
          else
            _buildList(context, palette),
        ],
      ),
    );
  }

  Widget _buildList(BuildContext context, AuraPalette palette) {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);

    final tasks = tasksAsync.value ?? [];
    final events = calendarAsync.value ?? [];

    final items =
        <_DueItem>[
          for (final t in tasks)
            if (!t.isCompleted)
              _DueItem(
                kind: _DueItemKind.task,
                title: t.title as String,
                time: t.dueDate as DateTime?,
                overdue:
                    t.dueDate != null &&
                    (t.dueDate as DateTime).isBefore(today),
              ),
          for (final e in events)
            if ((e.startTime as DateTime).isAfter(today) &&
                (e.startTime as DateTime).isBefore(
                  today.add(const Duration(days: 1)),
                ))
              _DueItem(
                kind: _DueItemKind.event,
                title: e.title as String,
                time: e.startTime as DateTime,
              ),
        ]..sort((a, b) {
          if (a.time == null) return 1;
          if (b.time == null) return -1;
          return a.time!.compareTo(b.time!);
        });

    final visible = items.take(5).toList();

    if (visible.isEmpty) {
      final l10n = AppLocalizations.of(context)!;
      return AuraEmptyState(
        icon: Icons.check_circle_outline,
        title: l10n.nothingDueToday,
        message: l10n.allCaughtUp,
      );
    }

    return Column(
      children: [
        for (final item in visible) ...[
          _DueRow(item: item),
          if (item != visible.last) const SizedBox(height: AuraSpacing.sm),
        ],
      ],
    );
  }
}

class _DueRow extends StatelessWidget {
  final _DueItem item;
  const _DueRow({required this.item});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final icon = item.kind == _DueItemKind.task
        ? Icons.circle_outlined
        : Icons.event_outlined;
    final color = item.overdue
        ? palette.danger
        : (item.kind == _DueItemKind.task
              ? palette.brandOnSurfaceText
              : palette.warning);

    return Semantics(
      label:
          '${item.kind == _DueItemKind.task ? "Task" : "Event"}: ${item.title}${item.overdue ? ", overdue" : ""}',
      child: Row(
        children: [
          Icon(icon, size: 16, color: color),
          const SizedBox(width: AuraSpacing.sm),
          Expanded(
            child: Text(
              item.title,
              style: Theme.of(
                context,
              ).textTheme.bodyMedium?.copyWith(color: palette.textPrimary),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (item.time != null)
            Text(
              DateFormat('h:mm a').format(item.time!),
              style: TextStyle(
                color: item.overdue ? palette.danger : palette.textSecondary,
                fontSize: 12,
                fontWeight: item.overdue ? FontWeight.w600 : FontWeight.w400,
              ),
            ),
        ],
      ),
    );
  }
}

class _QuickStatsRow extends StatelessWidget {
  final AsyncValue<List<dynamic>> tasksAsync;
  final AsyncValue<List<dynamic>> financeAsync;
  final AsyncValue<dynamic> healthAsync;
  const _QuickStatsRow({
    required this.tasksAsync,
    required this.financeAsync,
    required this.healthAsync,
  });

  @override
  Widget build(BuildContext context) {
    final activeTasks =
        tasksAsync.value?.where((t) => !t.isCompleted).length ?? 0;

    double expenses = 0;
    for (final r in financeAsync.value ?? []) {
      if (r.type == 'expense') expenses += r.amount as double;
    }
    final waterMl = healthAsync.value?.waterIntakeMl ?? 0;
    final l10n = AppLocalizations.of(context)!;

    final stats = [
      (Icons.playlist_add_check, l10n.activeTasks, '$activeTasks'),
      (Icons.water_drop_outlined, l10n.waterToday, '$waterMl mL'),
      (
        Icons.monetization_on_outlined,
        l10n.spentThisPeriod,
        '\$${expenses.toStringAsFixed(0)}',
      ),
    ];

    return Row(
      children: [
        for (final s in stats) ...[
          Expanded(
            child: _StatTile(icon: s.$1, label: s.$2, value: s.$3),
          ),
          if (s != stats.last) const SizedBox(width: AuraSpacing.md),
        ],
      ],
    );
  }
}

class _StatTile extends StatelessWidget {
  final IconData icon;
  final String label;
  final String value;
  const _StatTile({
    required this.icon,
    required this.label,
    required this.value,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return AuraCard(
      semanticLabel: '$label: $value',
      padding: const EdgeInsets.all(AuraSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 18, color: palette.brandOnSurfaceText),
          const SizedBox(height: AuraSpacing.sm),
          Text(
            value,
            style: Theme.of(context).textTheme.titleMedium,
            overflow: TextOverflow.ellipsis,
          ),
          Text(
            label,
            style: Theme.of(context).textTheme.labelSmall,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}

class _SystemHealthCard extends StatelessWidget {
  final AIRouterStatus aiStatus;
  const _SystemHealthCard({required this.aiStatus});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = AppLocalizations.of(context)!;
    // Reports whether the AI service is reachable, replacing an on-device model health probe.
    // Synchronous rather than an AsyncValue: the router records this as turns happen instead of
    // polling a daemon, so there is no loading state to render and no skeleton to show.
    final reachable = !aiStatus.isOffline;
    return AuraCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AuraSectionHeader(title: l10n.aiSystemStatus),
          const SizedBox(height: AuraSpacing.md),
          _StatusRow(
            label: l10n.serverInference,
            value: reachable ? l10n.statusReady : l10n.statusUnavailable,
            color: reachable ? palette.success : palette.danger,
          ),
          const SizedBox(height: AuraSpacing.sm),
          _StatusRow(
            label: l10n.localData,
            value: l10n.encryptedAesGcm,
            color: palette.textSecondary,
          ),
        ],
      ),
    );
  }
}

class _StatusRow extends StatelessWidget {
  final String label;
  final String value;
  final Color color;
  const _StatusRow({
    required this.label,
    required this.value,
    required this.color,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Row(
      children: [
        Expanded(
          child: Text(
            label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(color: palette.textSecondary, fontSize: 12),
          ),
        ),
        const SizedBox(width: AuraSpacing.sm),
        Flexible(
          child: Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            textAlign: TextAlign.end,
            style: TextStyle(
              color: color,
              fontWeight: FontWeight.w600,
              fontSize: 12,
            ),
          ),
        ),
      ],
    );
  }
}
