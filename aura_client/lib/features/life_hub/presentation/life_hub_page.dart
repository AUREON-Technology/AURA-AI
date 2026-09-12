import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import '../../calendar/data/calendar_repository.dart';
import '../../calendar/presentation/widgets/add_event_dialog.dart';
import '../../finance/data/finance_repository.dart';
import '../../finance/presentation/widgets/finance_summary_cards.dart';
import '../../finance/presentation/widgets/finance_transaction_form.dart';
import '../../finance/presentation/widgets/finance_transaction_history.dart';
import '../../health/data/health_repository.dart';
import '../../health/presentation/widgets/health_daily_dashboard.dart';
import '../../tasks/data/task_repository.dart';
import '../../tasks/presentation/widgets/add_task_dialog.dart';
import '../../tasks/presentation/widgets/empty_state_tasks.dart';
import '../../tasks/presentation/widgets/task_item_card.dart';
import '../../projects/data/project_providers.dart';
import '../../goals/data/goal_providers.dart';
import '../domain/life_item.dart';
import 'life_hub_view_model.dart';

class LifeHubPage extends ConsumerWidget {
  const LifeHubPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final filter = lifeHubFilterFromQuery(
      GoRouterState.of(context).uri.queryParameters['type'],
    );

    return Scaffold(
      backgroundColor: AuraPalette.of(context).background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text('Life Hub', style: Theme.of(context).textTheme.headlineMedium),
      ),
      floatingActionButton: filter == LifeHubFilter.health
          ? null
          : FloatingActionButton(
              heroTag: 'life_hub_add',
              onPressed: () => _onAdd(context, ref, filter),
              child: const Icon(Icons.add),
            ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.lg),
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(
                children: [
                  for (final value in LifeHubFilter.values)
                    Padding(
                      padding: const EdgeInsets.only(right: AuraSpacing.sm),
                      child: FilterChip(
                        label: Text(_label(value)),
                        selected: filter == value,
                        onSelected: (_) {
                          ref.read(lifeHubViewModelProvider.notifier).setFilter(value);
                          context.go('/life?type=${value.name}');
                        },
                      ),
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(height: AuraSpacing.md),
          Expanded(child: _LifeHubBody(filter: filter)),
        ],
      ),
    );
  }

  String _label(LifeHubFilter filter) {
    return switch (filter) {
      LifeHubFilter.all => 'All',
      LifeHubFilter.tasks => 'Tasks',
      LifeHubFilter.calendar => 'Calendar',
      LifeHubFilter.finance => 'Finance',
      LifeHubFilter.health => 'Health',
      LifeHubFilter.projects => 'Projects',
      LifeHubFilter.goals => 'Goals',
    };
  }

  Future<void> _onAdd(BuildContext context, WidgetRef ref, LifeHubFilter filter) async {
    switch (filter) {
      case LifeHubFilter.calendar:
        showAddEventDialog(context, ref, DateTime.now());
      case LifeHubFilter.finance:
        await showModalBottomSheet(
          context: context,
          isScrollControlled: true,
          builder: (_) => const FinanceTransactionForm(),
        );
      case LifeHubFilter.health:
        break;
      case LifeHubFilter.all:
      case LifeHubFilter.tasks:
        await showDialog(context: context, builder: (_) => const AddTaskDialog());
      case LifeHubFilter.projects:
        context.push('/projects');
      case LifeHubFilter.goals:
        context.push('/goals');
    }
  }
}

class _LifeHubBody extends ConsumerWidget {
  final LifeHubFilter filter;
  const _LifeHubBody({required this.filter});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return switch (filter) {
      LifeHubFilter.all => const _AgendaView(),
      LifeHubFilter.tasks => const _TasksPane(),
      LifeHubFilter.calendar => const _CalendarPane(),
      LifeHubFilter.finance => const _FinancePane(),
      LifeHubFilter.health => const _HealthPane(),
      LifeHubFilter.projects => const _ProjectsPane(),
      LifeHubFilter.goals => const _GoalsPane(),
    };
  }
}

class _AgendaView extends ConsumerWidget {
  const _AgendaView();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final items = ref.watch(lifeAgendaProvider);
    if (items.isEmpty) {
      return const AuraEmptyState(
        icon: Icons.event_available_outlined,
        title: 'Nothing on the board',
        message: 'Add a task, event, or log to see this week in one place.',
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.fromLTRB(AuraSpacing.lg, 0, AuraSpacing.lg, 88),
      itemCount: items.length,
      separatorBuilder: (_, _) => const SizedBox(height: AuraSpacing.sm),
      itemBuilder: (context, index) => _AgendaTile(item: items[index]),
    );
  }
}

class _AgendaTile extends StatelessWidget {
  final LifeItem item;
  const _AgendaTile({required this.item});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final (icon, color) = switch (item.type) {
      LifeItemType.task => (Icons.check_circle_outline, palette.brand),
      LifeItemType.event => (Icons.calendar_month_outlined, palette.info),
      LifeItemType.finance => (Icons.payments_outlined, palette.success),
      LifeItemType.health => (Icons.favorite_outline, palette.danger),
    };
    return AuraCard(
      child: Row(
        children: [
          CircleAvatar(
            backgroundColor: color.withValues(alpha: 0.12),
            child: Icon(icon, color: color, size: 18),
          ),
          const SizedBox(width: AuraSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  item.title,
                  style: Theme.of(context).textTheme.titleSmall?.copyWith(
                        decoration: item.done ? TextDecoration.lineThrough : null,
                      ),
                ),
                Text(
                  DateFormat('EEE, MMM d · h:mm a').format(item.when),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _TasksPane extends ConsumerWidget {
  const _TasksPane();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tasksAsync = ref.watch(tasksStreamProvider);
    return tasksAsync.when(
      data: (tasks) {
        if (tasks.isEmpty) return const EmptyStateTasks();
        return ListView.builder(
          padding: const EdgeInsets.fromLTRB(AuraSpacing.lg, 0, AuraSpacing.lg, 88),
          itemCount: tasks.length,
          itemBuilder: (context, index) => TaskItemCard(task: tasks[index]),
        );
      },
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => AuraEmptyState(
        icon: Icons.error_outline,
        title: 'Could not load tasks',
        message: e.toString(),
      ),
    );
  }
}

class _CalendarPane extends ConsumerWidget {
  const _CalendarPane();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final eventsAsync = ref.watch(calendarEventsStreamProvider);
    return eventsAsync.when(
      data: (events) {
        if (events.isEmpty) {
          return const AuraEmptyState(
            icon: Icons.calendar_month_outlined,
            title: 'No upcoming events',
          );
        }
        return ListView.separated(
          padding: const EdgeInsets.fromLTRB(AuraSpacing.lg, 0, AuraSpacing.lg, 88),
          itemCount: events.length,
          separatorBuilder: (_, _) => const SizedBox(height: AuraSpacing.sm),
          itemBuilder: (context, index) {
            final event = events[index];
            return AuraCard(
              child: ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(event.title),
                subtitle: Text(
                  DateFormat('EEE, MMM d · h:mm a').format(event.startTime),
                ),
              ),
            );
          },
        );
      },
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => AuraEmptyState(
        icon: Icons.error_outline,
        title: 'Could not load calendar',
        message: e.toString(),
      ),
    );
  }
}

class _FinancePane extends ConsumerWidget {
  const _FinancePane();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final statsAsync = ref.watch(financeStatsProvider);
    final txAsync = ref.watch(financeTransactionsStreamProvider);
    return ListView(
      padding: const EdgeInsets.fromLTRB(AuraSpacing.lg, 0, AuraSpacing.lg, 88),
      children: [
        statsAsync.when(
          data: (stats) => FinanceSummaryCards(stats: stats),
          loading: () => const AuraSkeletonCard(),
          error: (e, _) => Text('$e'),
        ),
        const SizedBox(height: AuraSpacing.lg),
        FinanceTransactionHistory(transactionsAsync: txAsync),
      ],
    );
  }
}

class _HealthPane extends ConsumerWidget {
  const _HealthPane();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final healthAsync = ref.watch(dailyHealthStreamProvider(DateTime.now()));
    return healthAsync.when(
      data: (record) => SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(AuraSpacing.lg, 0, AuraSpacing.lg, 24),
        child: HealthDailyDashboard(record: record),
      ),
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => AuraEmptyState(
        icon: Icons.error_outline,
        title: 'Could not load health',
        message: e.toString(),
      ),
    );
  }
}

class _ProjectsPane extends ConsumerWidget {
  const _ProjectsPane();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final projectsAsync = ref.watch(projectsStreamProvider);
    return projectsAsync.when(
      data: (projects) {
        if (projects.isEmpty) {
          return const AuraEmptyState(
            icon: Icons.folder_open,
            title: 'No projects yet',
            message: 'Create a project to organize your work.',
          );
        }
        return ListView.builder(
          padding: const EdgeInsets.fromLTRB(AuraSpacing.lg, 0, AuraSpacing.lg, 88),
          itemCount: projects.length,
          itemBuilder: (context, index) {
            final project = projects[index];
            return AuraCard(
              child: ListTile(
                leading: CircleAvatar(
                  backgroundColor: Colors.blue.withValues(alpha: 0.12),
                  child: const Icon(Icons.folder, color: Colors.blue),
                ),
                title: Text(project.title, style: const TextStyle(fontWeight: FontWeight.w600)),
                subtitle: project.targetDate != null
                    ? Text('Due: ${project.targetDate!.toLocal().toString().split(' ')[0]}')
                    : null,
              ),
            );
          },
        );
      },
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => AuraEmptyState(
        icon: Icons.error_outline,
        title: 'Could not load projects',
        message: e.toString(),
      ),
    );
  }
}

class _GoalsPane extends ConsumerWidget {
  const _GoalsPane();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final goalsAsync = ref.watch(goalsStreamProvider);
    return goalsAsync.when(
      data: (goals) {
        if (goals.isEmpty) {
          return const AuraEmptyState(
            icon: Icons.flag,
            title: 'No goals yet',
            message: 'Create a goal to track your aspirations.',
          );
        }
        return ListView.builder(
          padding: const EdgeInsets.fromLTRB(AuraSpacing.lg, 0, AuraSpacing.lg, 88),
          itemCount: goals.length,
          itemBuilder: (context, index) {
            final goal = goals[index];
            return AuraCard(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(goal.title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 16)),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Chip(
                          label: Text(goal.status, style: const TextStyle(fontSize: 11)),
                          backgroundColor: Colors.blue.withValues(alpha: 0.1),
                        ),
                        const SizedBox(width: 8),
                        Chip(
                          label: Text(goal.priority, style: const TextStyle(fontSize: 11)),
                          backgroundColor: Colors.orange.withValues(alpha: 0.1),
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    LinearProgressIndicator(
                      value: goal.progressPct / 100,
                      backgroundColor: Colors.grey[200],
                      valueColor: const AlwaysStoppedAnimation<Color>(Colors.blue),
                      minHeight: 8,
                    ),
                    const SizedBox(height: 4),
                    Text('${goal.progressPct}% complete'),
                    if (goal.targetDate != null) ...[
                      const SizedBox(height: 4),
                      Text('Target: ${goal.targetDate!.toLocal().toString().split(' ')[0]}', style: TextStyle(fontSize: 12, color: Colors.grey[600])),
                    ],
                  ],
                ),
              ),
            );
          },
        );
      },
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => AuraEmptyState(
        icon: Icons.error_outline,
        title: 'Could not load goals',
        message: e.toString(),
      ),
    );
  }
}
