import 'package:flutter/foundation.dart';

import 'package:aura_client/l10n/l10n.dart';

String pendingActionLabel(dynamic type, dynamic params) {
  final t = type?.toString() ?? '';
  final p = params is Map ? params : const {};
  final l10n = currentL10n;
  if (t == 'add_task') return l10n.chatActionCreateTask('${p['title']}');
  if (t == 'add_event') return l10n.chatActionScheduleEvent('${p['title']}');
  if (t == 'add_expense') return l10n.chatActionLogExpense('${p['amount']}');
  if (t == 'add_income') return l10n.chatActionLogIncome('${p['amount']}');
  if (t == 'log_health') return l10n.chatActionLogHealth;
  return l10n.chatActionUnknownNamed(t);
}

class PendingActionsOutcome {
  final int succeeded;
  final List<String> failedLabels;

  const PendingActionsOutcome({
    required this.succeeded,
    required this.failedLabels,
  });

  bool get allSucceeded => failedLabels.isEmpty;
  bool get allFailed => succeeded == 0 && failedLabels.isNotEmpty;
}

typedef AddTaskFn = Future<void> Function({
  required String title,
  String? description,
  required String priority,
  DateTime? dueDate,
  required String category,
});

typedef AddEventFn = Future<void> Function({
  required String title,
  String? description,
  required DateTime startTime,
  required DateTime endTime,
});

typedef AddTransactionFn = Future<void> Function({
  required String type,
  required double amount,
  required String category,
  String? description,
  required DateTime date,
});

typedef LogIntMetricFn = Future<void> Function(DateTime date, int value);
typedef LogWeightFn = Future<void> Function(DateTime date, double weight);

class PendingActionsExecutor {
  final AddTaskFn addTask;
  final AddEventFn addEvent;
  final AddTransactionFn addTransaction;
  final LogIntMetricFn logWater;
  final LogIntMetricFn logSleep;
  final LogIntMetricFn logCalories;
  final LogIntMetricFn logExercise;
  final LogWeightFn logWeight;

  const PendingActionsExecutor({
    required this.addTask,
    required this.addEvent,
    required this.addTransaction,
    required this.logWater,
    required this.logSleep,
    required this.logCalories,
    required this.logExercise,
    required this.logWeight,
  });

  Future<PendingActionsOutcome> execute(List<dynamic> actions) async {
    final failed = <String>[];
    var succeeded = 0;

    for (final act in actions) {
      if (act is! Map) {
        failed.add(currentL10n.chatActionUnknown);
        continue;
      }
      final type = act['type'] ?? '';
      final params = act['params'] ?? {};
      final label = pendingActionLabel(type, params);

      try {
        await _executeOne(type.toString(), params is Map ? params : const {});
        succeeded++;
      } catch (e) {
        debugPrint('Action execution failed: $e');
        failed.add(label);
      }
    }

    return PendingActionsOutcome(succeeded: succeeded, failedLabels: failed);
  }

  Future<void> _executeOne(String type, Map<dynamic, dynamic> params) async {
    switch (type) {
      case 'add_task':
        DateTime? due;
        if (params['dueDate'] != null) due = DateTime.tryParse(params['dueDate'].toString());
        await addTask(
          title: params['title'] ?? 'Task',
          priority: params['priority'] ?? 'medium',
          category: params['category'] ?? 'personal',
          dueDate: due,
        );
        return;
      case 'add_event':
        final start = DateTime.tryParse(params['startTime']?.toString() ?? '') ?? DateTime.now();
        final parsedEnd = params['endTime'] != null ? DateTime.tryParse(params['endTime'].toString()) : null;
        await addEvent(
          title: params['title'] ?? 'Event',
          description: params['description'],
          startTime: start,
          endTime: parsedEnd ?? start.add(const Duration(hours: 1)),
        );
        return;
      case 'add_expense':
        await addTransaction(
          type: 'expense',
          amount: (params['amount'] as num).toDouble(),
          category: params['category'] ?? 'Other',
          description: params['description'],
          date: DateTime.now(),
        );
        return;
      case 'add_income':
        await addTransaction(
          type: 'income',
          amount: (params['amount'] as num).toDouble(),
          category: params['category'] ?? 'Other',
          description: params['description'],
          date: DateTime.now(),
        );
        return;
      case 'log_health':
        final now = DateTime.now();
        if (params['waterIntakeMl'] != null) {
          await logWater(now, params['waterIntakeMl'] as int);
        }
        if (params['sleepMinutes'] != null) {
          await logSleep(now, params['sleepMinutes'] as int);
        }
        if (params['caloriesConsumed'] != null) {
          await logCalories(now, params['caloriesConsumed'] as int);
        }
        if (params['exerciseMinutes'] != null) {
          await logExercise(now, params['exerciseMinutes'] as int);
        }
        if (params['weight'] != null) {
          await logWeight(now, (params['weight'] as num).toDouble());
        }
        return;
      default:
        throw StateError('Unknown action type: $type');
    }
  }
}
