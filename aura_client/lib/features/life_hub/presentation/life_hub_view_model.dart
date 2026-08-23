import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../calendar/data/calendar_repository.dart';
import '../../finance/data/finance_repository.dart';
import '../../health/data/health_repository.dart';
import '../../tasks/data/task_repository.dart';
import '../domain/life_item.dart';

enum LifeHubFilter { all, tasks, calendar, finance, health }

LifeHubFilter lifeHubFilterFromQuery(String? raw) {
  return switch (raw) {
    'tasks' => LifeHubFilter.tasks,
    'calendar' => LifeHubFilter.calendar,
    'finance' => LifeHubFilter.finance,
    'health' => LifeHubFilter.health,
    _ => LifeHubFilter.all,
  };
}

class LifeHubViewModel extends Notifier<LifeHubFilter> {
  @override
  LifeHubFilter build() => LifeHubFilter.all;

  void setFilter(LifeHubFilter filter) => state = filter;
}

final lifeHubViewModelProvider =
    NotifierProvider<LifeHubViewModel, LifeHubFilter>(LifeHubViewModel.new);

final lifeAgendaProvider = Provider<List<LifeItem>>((ref) {
  final tasks = ref.watch(tasksStreamProvider).value ?? const [];
  final events = ref.watch(calendarEventsStreamProvider).value ?? const [];
  final finance = ref.watch(financeTransactionsStreamProvider).value ?? const [];
  final health = ref.watch(dailyHealthStreamProvider(DateTime.now())).value;

  final items = <LifeItem>[
    for (final t in tasks)
      LifeItem(
        id: t.id,
        type: LifeItemType.task,
        title: t.title,
        subtitle: t.category,
        when: t.dueDate ?? t.createdAt,
        done: t.isCompleted,
      ),
    for (final e in events)
      LifeItem(
        id: e.id,
        type: LifeItemType.event,
        title: e.title,
        subtitle: e.description,
        when: e.startTime,
      ),
    for (final f in finance)
      LifeItem(
        id: f.id,
        type: LifeItemType.finance,
        title: '${f.type} · ${f.category}',
        subtitle: f.description,
        when: f.date,
      ),
    if (health != null)
      LifeItem(
        id: health.id,
        type: LifeItemType.health,
        title: 'Today’s health log',
        subtitle: '${health.waterIntakeMl} ml water',
        when: health.recordDate,
      ),
  ];
  items.sort((a, b) => a.when.compareTo(b.when));
  return items;
});
