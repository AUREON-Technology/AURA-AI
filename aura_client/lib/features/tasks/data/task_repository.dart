import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:googleapis/tasks/v1.dart' as tasks_api;
import 'package:extension_google_sign_in_as_googleapis_auth/extension_google_sign_in_as_googleapis_auth.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/sync/sync_payloads.dart';
import 'package:aura_client/core/undo/undoable_deletes.dart';

class TaskRepository {
  final AppDatabase _db;
  final _uuid = const Uuid();

  TaskRepository(this._db);

  /// Reads the row back and enqueues it for the next SyncClient.push() —
  /// see Doc/V2_BLUEPRINT.md §5. Reading back (rather than constructing the
  /// payload inline at each call site) guarantees the outbox always
  /// reflects exactly what's in Drift.
  Future<void> _enqueueUpsert(String id) async {
    final row = await (_db.select(_db.tasks)..where((t) => t.id.equals(id))).getSingleOrNull();
    if (row == null) return;
    await _db.enqueueOutbox(table: 'tasks', recordId: id, operation: 'upsert', payload: SyncPayloads.task(row));
  }

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
    final id = _uuid.v4();
    final task = TasksCompanion.insert(
      id: id,
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
    await _enqueueUpsert(id);
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
    await _enqueueUpsert(id);
  }

  // Delete a task. The outbox 'delete' op (soft-deleted server-side, see
  // api/v2/sync.py) fully supersedes the old PendingDeletes-tombstone
  // approach: deleted rows are still returned on subsequent pulls with
  // `deleted_at` set, so every device (including this one) converges
  // without needing a separate local "was this deleted" table.
  Future<void> deleteTask(String id) async {
    await (_db.delete(_db.tasks)..where((t) => t.id.equals(id))).go();
    await _db.enqueueOutbox(table: 'tasks', recordId: id, operation: 'delete');
  }

  // Update a task fully
  Future<void> updateTask(Task task) async {
    final updated = task.copyWith(
      isSynced: false,
      updatedAt: Value(DateTime.now()),
    );
    await _db.update(_db.tasks).replace(updated);
    await _enqueueUpsert(task.id);
  }

  // Bidirectional Google Tasks Sync
  Future<List<String>> syncGoogleTasks({required bool isMock}) async {
    final logs = <String>[];
    logs.add("Initializing Tasks sync process...");

    if (isMock) {
      await Future.delayed(const Duration(milliseconds: 1500));
      logs.add("Connected to local offline tasks provider.");
      
      // Seed some mock remote tasks to simulate sync
      final sampleTasks = [
        TasksCompanion.insert(
          id: _uuid.v4(),
          title: "Complete AURA RAG implementation",
          description: const Value("Verify document chunks, embeddings, and Qdrant retrieval"),
          priority: "high",
          dueDate: Value(DateTime.now().add(const Duration(days: 2))),
          category: "project",
          isCompleted: false,
          createdAt: DateTime.now(),
          googleTaskId: const Value("mock_g_task_1"),
        ),
        TasksCompanion.insert(
          id: _uuid.v4(),
          title: "Review flutter layout optimization guidelines",
          description: const Value("Avoid unnecessary rebuilds, use const constructors"),
          priority: "medium",
          dueDate: Value(DateTime.now().add(const Duration(days: 4))),
          category: "personal",
          isCompleted: true,
          createdAt: DateTime.now(),
          googleTaskId: const Value("mock_g_task_2"),
        ),
      ];

      for (final task in sampleTasks) {
        final existing = await (_db.select(_db.tasks)
              ..where((t) => t.googleTaskId.equals(task.googleTaskId.value!)))
            .getSingleOrNull();

        if (existing == null) {
          await _db.into(_db.tasks).insert(task);
          await _enqueueUpsert(task.id.value);
          logs.add("Imported mock task: ${task.title.value}");
        }
      }
      logs.add("Sync simulated successfully.");
      return logs;
    }

    try {
      final googleSignIn = GoogleSignIn.instance;
      final scopes = ['https://www.googleapis.com/auth/tasks'];

      logs.add("Requesting Google Tasks authorization...");
      final GoogleSignInClientAuthorization authorization =
          await googleSignIn.authorizationClient.authorizeScopes(scopes);

      final client = authorization.authClient(scopes: scopes);
      final tasksApi = tasks_api.TasksApi(client);
      logs.add("Established connection with Google Tasks API.");

      // 1. Get AURA Tasks list or create one if not existing
      final taskLists = await tasksApi.tasklists.list();
      String? listId;
      
      final lists = taskLists.items ?? [];
      for (final l in lists) {
        if (l.title == "AURA Tasks") {
          listId = l.id;
          break;
        }
      }

      if (listId == null) {
        logs.add("Creating a new 'AURA Tasks' task list on Google...");
        final newList = await tasksApi.tasklists.insert(tasks_api.TaskList()..title = "AURA Tasks");
        listId = newList.id;
        logs.add("Created 'AURA Tasks' task list.");
      } else {
        logs.add("Found existing 'AURA Tasks' list.");
      }

      if (listId == null) throw Exception("Failed to resolve Google Task List.");

      // 2. Fetch Tasks from Google List
      final gTasksList = await tasksApi.tasks.list(listId, showCompleted: true, showHidden: true);
      final remoteTasks = gTasksList.items ?? [];
      logs.add("Retrieved ${remoteTasks.length} tasks from Google Cloud Tasks.");

      final resolvedListId = listId;
      await reconcileGoogleTasks(
        remoteTasks,
        logs: logs,
        insertRemote: (task) async => (await tasksApi.tasks.insert(task, resolvedListId)).id,
        updateRemote: (googleId, task) => tasksApi.tasks.update(task, resolvedListId, googleId),
      );

      logs.add("Google Tasks sync completed successfully!");
    } catch (e) {
      logs.add("Sync error occurred: $e");
    }

    return logs;
  }

  /// Steps 3-5 of [syncGoogleTasks], against Google through [insertRemote] / [updateRemote].
  ///
  /// Every change this makes to a local task is also queued for the AURA backend (F24): an
  /// imported, updated or removed task used to change only on this device, so the server, the
  /// assistant and every other device never heard of it. `isSynced` here means "matches Google"
  /// (step 5 reads it); backend sync is the outbox's job and is never skipped because of it.
  @visibleForTesting
  Future<void> reconcileGoogleTasks(
    List<tasks_api.Task> remoteTasks, {
    required List<String> logs,
    required Future<String?> Function(tasks_api.Task task) insertRemote,
    required Future<void> Function(String googleId, tasks_api.Task task) updateRemote,
  }) async {
    // 3. Process remote tasks and sync locally (LWW)
    for (final gt in remoteTasks) {
      if (gt.id == null || gt.title == null) continue;

      final isCompleted = gt.status == 'completed';
      final isDeleted = gt.deleted == true || gt.hidden == true;
      
      DateTime? due;
      if (gt.due != null) {
        due = DateTime.tryParse(gt.due!);
      }

      final existing = await (_db.select(_db.tasks)
            ..where((t) => t.googleTaskId.equals(gt.id!)))
          .getSingleOrNull();

      if (isDeleted) {
        if (existing != null) {
          await (_db.delete(_db.tasks)..where((t) => t.id.equals(existing.id))).go();
          await _db.enqueueOutbox(table: 'tasks', recordId: existing.id, operation: 'delete');
          logs.add("Removed deleted task locally: ${gt.title}");
        }
        continue;
      }

      final remoteUpdatedAt = gt.updated != null ? DateTime.tryParse(gt.updated!) : null;

      final companion = TasksCompanion(
        id: existing != null ? Value(existing.id) : Value(_uuid.v4()),
        title: Value(gt.title!),
        description: Value(gt.notes),
        priority: Value(existing?.priority ?? 'medium'),
        dueDate: Value(due),
        category: Value(existing?.category ?? 'personal'),
        isCompleted: Value(isCompleted),
        createdAt: Value(existing?.createdAt ?? DateTime.now()),
        googleTaskId: Value(gt.id),
        isSynced: const Value(true),
        updatedAt: Value(remoteUpdatedAt ?? DateTime.now()),
      );

      if (existing != null) {
        // LWW Conflict Resolution
        final localUpdatedAt = existing.updatedAt;
        if (remoteUpdatedAt != null && (localUpdatedAt == null || remoteUpdatedAt.isAfter(localUpdatedAt))) {
          await (_db.update(_db.tasks)..where((t) => t.id.equals(existing.id))).write(companion);
          await _enqueueUpsert(existing.id);
        }
      } else {
        await _db.into(_db.tasks).insert(companion);
        await _enqueueUpsert(companion.id.value);
        logs.add("Imported task: ${gt.title}");
      }
    }

    // 4. Upload new local tasks (lacking googleTaskId)
    final unsyncedTasks = await (_db.select(_db.tasks)..where((t) => t.googleTaskId.isNull())).get();
    if (unsyncedTasks.isNotEmpty) {
      logs.add("Uploading ${unsyncedTasks.length} new local tasks...");
      for (final lt in unsyncedTasks) {
        final newTask = tasks_api.Task()
          ..title = lt.title
          ..notes = lt.description
          ..status = lt.isCompleted ? 'completed' : 'needsAction';
        
        if (lt.dueDate != null) {
          newTask.due = lt.dueDate!.toUtc().toIso8601String();
        }

        try {
          final createdId = await insertRemote(newTask);
          if (createdId != null) {
            await (_db.update(_db.tasks)..where((t) => t.id.equals(lt.id))).write(
              TasksCompanion(
                googleTaskId: Value(createdId),
                isSynced: const Value(true),
              ),
            );
            // The link to Google travels too, or another device would import it a second time.
            await _enqueueUpsert(lt.id);
            logs.add("Uploaded task: ${lt.title}");
          }
        } catch (uploadError) {
          logs.add("Failed uploading '${lt.title}': $uploadError");
        }
      }
    }

    // 5. Sync updates to already synced tasks (where local isSynced is false and googleTaskId is not null)
    final modifiedTasks = await (_db.select(_db.tasks)..where((t) => t.googleTaskId.isNotNull() & t.isSynced.equals(false))).get();
    if (modifiedTasks.isNotEmpty) {
      logs.add("Syncing ${modifiedTasks.length} modified tasks...");
      for (final mt in modifiedTasks) {
        final updatedTask = tasks_api.Task()
          ..title = mt.title
          ..notes = mt.description
          ..status = mt.isCompleted ? 'completed' : 'needsAction';

        if (mt.dueDate != null) {
          updatedTask.due = mt.dueDate!.toUtc().toIso8601String();
        }

        try {
          await updateRemote(mt.googleTaskId!, updatedTask);
          await (_db.update(_db.tasks)..where((t) => t.id.equals(mt.id))).write(
            const TasksCompanion(isSynced: Value(true)),
          );
        } catch (updateError) {
          logs.add("Failed updating task '${mt.title}': $updateError");
        }
      }
    }
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

// Stream Provider for tasks. A task whose delete is still inside its undo
// window is left out here, so every list built on this hides it at once
// without the row being deleted yet (see undoable_deletes.dart).
final tasksStreamProvider = StreamProvider.autoDispose<List<Task>>((ref) {
  final repository = ref.watch(taskRepositoryProvider);
  final filter = ref.watch(taskCategoryFilterProvider);
  final pendingDeletes = ref.watch(undoableDeletesProvider);
  final tasks = repository.watchTasks(categoryFilter: filter);
  if (pendingDeletes.isEmpty) return tasks;
  return tasks.map(
    (list) => list.where((t) => !pendingDeletes.contains(taskUndoKey(t.id))).toList(),
  );
});
