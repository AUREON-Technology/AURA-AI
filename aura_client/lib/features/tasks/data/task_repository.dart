import 'package:drift/drift.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import '../../../core/database/database.dart';
import '../../../core/database/database_provider.dart';

class TaskRepository {
  final AppDatabase _db;
  final _uuid = const Uuid();

  TaskRepository(this._db);

  // Watch tasks filtered by category or all tasks if null/all
  Stream<List<Task>> watchTasks({String? categoryFilter}) {
    if (categoryFilter == null || categoryFilter == 'All') {
      return (_db.select(_db.tasks)
            ..orderBy([
              (t) => OrderingTerm(expression: t.isCompleted, mode: OrderingMode.asc),
              (t) => OrderingTerm(expression: t.dueDate, mode: OrderingMode.asc),
              (t) => OrderingTerm(expression: t.createdAt, mode: OrderingMode.desc),
            ]))
          .watch();
    } else {
      return (_db.select(_db.tasks)
            ..where((t) => t.category.equals(categoryFilter.toLowerCase()))
            ..orderBy([
              (t) => OrderingTerm(expression: t.isCompleted, mode: OrderingMode.asc),
              (t) => OrderingTerm(expression: t.dueDate, mode: OrderingMode.asc),
              (t) => OrderingTerm(expression: t.createdAt, mode: OrderingMode.desc),
            ]))
          .watch();
    }
  }

  // Insert a task
  Future<void> addTask({
    required String title,
    String? description,
    required String priority,
    DateTime? dueDate,
    required String category,
  }) async {
    final task = TasksCompanion.insert(
      id: _uuid.v4(),
      title: title,
      description: Value(description),
      priority: priority.toLowerCase(),
      dueDate: Value(dueDate),
      category: category.toLowerCase(),
      isCompleted: false,
      createdAt: DateTime.now(),
      isSynced: const Value(false),
      updatedAt: Value(DateTime.now()),
    );
    await _db.into(_db.tasks).insert(task);
  }

  // Toggle task complete status
  Future<void> toggleTaskComplete(String id, bool isCompleted) async {
    await (_db.update(_db.tasks)..where((t) => t.id.equals(id))).write(
      TasksCompanion(
        isCompleted: Value(!isCompleted),
        isSynced: const Value(false),
        updatedAt: Value(DateTime.now()),
      ),
    );
  }

  // Delete a task
  Future<void> deleteTask(String id) async {
    // Log pending deletion first
    await _db.into(_db.pendingDeletes).insert(
      PendingDeletesCompanion.insert(
        recordId: id,
        targetTable: 'tasks',
        deletedAt: DateTime.now(),
      ),
    );
    await (_db.delete(_db.tasks)..where((t) => t.id.equals(id))).go();
  }

  // Update a task fully
  Future<void> updateTask(Task task) async {
    final updated = task.copyWith(
      isSynced: false,
      updatedAt: Value(DateTime.now()),
    );
    await _db.update(_db.tasks).replace(updated);
  }
}

final taskRepositoryProvider = Provider<TaskRepository>((ref) {
  final db = ref.watch(databaseProvider);
  return TaskRepository(db);
});

// Category filter state provider
class TaskCategoryFilter extends Notifier<String> {
  @override
  String build() => 'All';
  void setFilter(String category) => state = category;
}

final taskCategoryFilterProvider = NotifierProvider<TaskCategoryFilter, String>(() {
  return TaskCategoryFilter();
});

// Stream Provider for tasks
final tasksStreamProvider = StreamProvider.autoDispose<List<Task>>((ref) {
  final repository = ref.watch(taskRepositoryProvider);
  final filter = ref.watch(taskCategoryFilterProvider);
  return repository.watchTasks(categoryFilter: filter);
});
