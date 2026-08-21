import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/health_repository.dart';

class HealthGoals {
  static const waterGoalMl = 2000;
  static const sleepGoalMins = 480;
  static const exerciseGoalMins = 30;
  static const calorieGoalKcal = 2000;
}

double goalProgress(int current, int target) {
  if (target <= 0) return 0;
  return (current / target).clamp(0.0, 1.0);
}

class HealthViewModel extends Notifier<DateTime> {
  @override
  DateTime build() => DateTime.now();

  DateTime get selectedDate => state;

  Future<void> logWater(int ml) => ref.read(healthRepositoryProvider).logWater(state, ml);
  Future<void> logSleep(int minutes) => ref.read(healthRepositoryProvider).logSleep(state, minutes);
  Future<void> logExercise(int minutes) => ref.read(healthRepositoryProvider).logExercise(state, minutes);
  Future<void> logCalories(int kcal) => ref.read(healthRepositoryProvider).logCalories(state, kcal);
  Future<void> logWeight(double kg) => ref.read(healthRepositoryProvider).logWeight(state, kg);
}

final healthViewModelProvider = NotifierProvider<HealthViewModel, DateTime>(HealthViewModel.new);
