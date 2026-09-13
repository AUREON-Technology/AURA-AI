import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/legacy.dart';
import 'package:aura_client/core/database/database.dart';
import '../data/goal_repository.dart';

final goalsStreamProvider = StreamProvider<List<Goal>>((ref) {
  final repo = ref.watch(goalRepositoryProvider);
  return repo.watchGoals();
});

final goalsFilterProvider = StateProvider<String?>((ref) => null);