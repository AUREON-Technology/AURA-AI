import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/features/goals/data/goal_repository.dart';

class GoalsViewModel extends StateNotifier<AsyncValue<void>> {
  final GoalRepository _repo;

  GoalsViewModel(this._repo) : super(const AsyncValue.data(null));

  Future<void> addGoal({
    required String title,
    String? description,
    String? projectId,
    String? category,
    required String status,
    required String priority,
    DateTime? targetDate,
    int progressPct = 0,
  }) async {
    state = const AsyncValue.loading();
    try {
      await _repo.addGoal(
        title: title,
        description: description,
        projectId: projectId,
        category: category,
        status: status,
        priority: priority,
        targetDate: targetDate,
        progressPct: progressPct,
      );
      state = const AsyncValue.data(null);
    } catch (e, st) {
      state = AsyncValue.error(e, st);
    }
  }

  Future<void> updateGoalStatus(String id, String status) async {
    await _repo.updateGoalStatus(id, status);
  }

  Future<void> updateGoalProgress(String id, int progressPct) async {
    await _repo.updateGoalProgress(id, progressPct);
  }

  Future<void> deleteGoal(String id) async {
    await _repo.deleteGoal(id);
  }
}

final goalsViewModelProvider = StateNotifierProvider<GoalsViewModel, AsyncValue<void>>((ref) {
  final repo = ref.watch(goalRepositoryProvider);
  return GoalsViewModel(repo);
});