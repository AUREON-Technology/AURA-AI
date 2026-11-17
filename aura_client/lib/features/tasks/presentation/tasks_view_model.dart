import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_provider.dart';
import '../data/task_repository.dart';
import 'package:aura_client/l10n/l10n.dart';

@immutable
class TasksUiState {
  final bool isSyncing;
  final List<String> syncLogs;

  const TasksUiState({
    this.isSyncing = false,
    this.syncLogs = const [],
  });

  bool get syncHadErrors => syncLogs.any((l) => l.toLowerCase().contains('error'));

  TasksUiState copyWith({
    bool? isSyncing,
    List<String>? syncLogs,
  }) {
    return TasksUiState(
      isSyncing: isSyncing ?? this.isSyncing,
      syncLogs: syncLogs ?? this.syncLogs,
    );
  }
}

class TasksViewModel extends Notifier<TasksUiState> {
  @override
  TasksUiState build() => const TasksUiState();

  void setCategoryFilter(String category) {
    ref.read(taskCategoryFilterProvider.notifier).setFilter(category);
  }

  Future<void> toggleComplete(String id, bool isCompleted) {
    return ref.read(taskRepositoryProvider).toggleTaskComplete(id, isCompleted);
  }

  Future<void> deleteTask(String id) {
    return ref.read(taskRepositoryProvider).deleteTask(id);
  }

  Future<List<String>?> syncGoogleTasks() async {
    if (state.isSyncing) return null;
    state = state.copyWith(isSyncing: true, syncLogs: const ['Starting synchronization...']);

    final auth = ref.read(authProvider).value;
    final isMock = auth?.id == 'mock_user_123' || auth == null;

    try {
      final logs = await ref.read(taskRepositoryProvider).syncGoogleTasks(isMock: isMock);
      state = state.copyWith(isSyncing: false, syncLogs: logs);
      return logs;
    } catch (e) {
      final logs = [...state.syncLogs, 'error: $e'];
      state = state.copyWith(isSyncing: false, syncLogs: logs);
      return logs;
    }
  }
}

final tasksViewModelProvider = NotifierProvider<TasksViewModel, TasksUiState>(TasksViewModel.new);

/// Display label for a task category value ('All' filter included). The stored value and the
/// filter value stay English; unknown values are shown as stored.
String taskCategoryLabel(AppLocalizations l10n, String category) {
  switch (category.toLowerCase()) {
    case 'all':
      return l10n.tasksCategoryAll;
    case 'personal':
      return l10n.tasksCategoryPersonal;
    case 'academic':
      return l10n.tasksCategoryAcademic;
    case 'client':
      return l10n.tasksCategoryClient;
    case 'project':
      return l10n.tasksCategoryProject;
    default:
      return category;
  }
}

/// Display label for a stored task priority; unknown values are shown as stored.
String taskPriorityLabel(AppLocalizations l10n, String priority) {
  switch (priority.toLowerCase()) {
    case 'low':
      return l10n.tasksPriorityLow;
    case 'medium':
      return l10n.tasksPriorityMedium;
    case 'high':
      return l10n.tasksPriorityHigh;
    default:
      return priority;
  }
}
