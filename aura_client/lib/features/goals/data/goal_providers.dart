import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../data/goal_repository.dart';

final goalsStreamProvider = StreamProvider<List<Goal>>((ref) {
  final repo = ref.watch(goalRepositoryProvider);
  return repo.watchGoals();
});

final goalsFilterProvider = StateProvider<String?>((ref) => null);