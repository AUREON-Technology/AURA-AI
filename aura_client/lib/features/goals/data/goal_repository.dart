import 'package:drift/drift.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/sync/sync_payloads.dart';

class GoalRepository {
  final AppDatabase _db;
  final _uuid = const Uuid();

  GoalRepository(this._db);

  /// Reads the row back and enqueues it for the next SyncClient.push()
  Future<void> _enqueueUpsert(String id) async {
    final row = await (_db.select(_db.goals)..where((t) => t.id.equals(id))).getSingleOrNull();
    if (row == null) return;
    await _db.enqueueOutbox(table: 'goals', recordId: id, operation: 'upsert', payload: SyncPayloads.goal(row));
  }

  /// Watch all goals, optionally filtered by project
  Stream<List<Goal>> watchGoals({String? projectId}) {
    if (projectId == null) {
      return (_db.select(_db.goals)
            ..orderBy([
              (t) => OrderingTerm(expression: t.status, mode: OrderingMode.asc),
              (t) => OrderingTerm(expression: t.priority, mode: OrderingMode.desc),
              (t) => OrderingTerm(expression: t.targetDate, mode: OrderingMode.asc),
              (t) => OrderingTerm(expression: t.createdAt, mode: OrderingMode.desc),
            ]))
          .watch();
    } else {
      return (_db.select(_db.goals)
            ..where((t) => t.projectId.equals(projectId))
            ..orderBy([
              (t) => OrderingTerm(expression: t.status, mode: OrderingMode.asc),
              (t) => OrderingTerm(expression: t.priority, mode: OrderingMode.desc),
              (t) => OrderingTerm(expression: t.targetDate, mode: OrderingMode.asc),
              (t) => OrderingTerm(expression: t.createdAt, mode: OrderingMode.desc),
            ]))
          .watch();
    }
  }

  /// Insert a goal
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
    final id = _uuid.v4();
    final goal = GoalsCompanion.insert(
      id: id,
      title: title,
      description: Value(description),
      projectId: Value(projectId),
      category: Value(category),
      status: status,
      priority: priority,
      targetDate: Value(targetDate),
      progressPct: Value(progressPct),
      createdAt: DateTime.now(),
      isSynced: const Value(false),
      updatedAt: Value(DateTime.now()),
    );
    await _db.into(_db.goals).insert(goal);
    await _enqueueUpsert(id);
  }

  /// Update goal status
  Future<void> updateGoalStatus(String id, String status) async {
    await (_db.update(_db.goals)..where((t) => t.id.equals(id))).write(
      GoalsCompanion(
        status: Value(status),
        isSynced: const Value(false),
        updatedAt: Value(DateTime.now()),
      ),
    );
    await _enqueueUpsert(id);
  }

  /// Update goal progress
  Future<void> updateGoalProgress(String id, int progressPct) async {
    await (_db.update(_db.goals)..where((t) => t.id.equals(id))).write(
      GoalsCompanion(
        progressPct: Value(progressPct.clamp(0, 100)),
        isSynced: const Value(false),
        updatedAt: Value(DateTime.now()),
      ),
    );
    await _enqueueUpsert(id);
  }

  /// Update goal fully
  Future<void> updateGoal(Goal goal) async {
    final updated = goal.copyWith(
      isSynced: false,
      updatedAt: Value(DateTime.now()),
    );
    await _db.into(_db.goals).insertOnConflictUpdate(updated);
    await _enqueueUpsert(goal.id);
  }

  /// Delete a goal
  Future<void> deleteGoal(String id) async {
    await (_db.delete(_db.goals)..where((t) => t.id.equals(id))).go();
    await _db.enqueueOutbox(table: 'goals', recordId: id, operation: 'delete');
  }
}

final goalRepositoryProvider = Provider<GoalRepository>((ref) {
  final db = ref.watch(databaseProvider);
  return GoalRepository(db);
});