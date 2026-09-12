import 'package:drift/drift.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/sync/sync_payloads.dart';

class ProjectRepository {
  final AppDatabase _db;
  final _uuid = const Uuid();

  ProjectRepository(this._db);

  /// Reads the row back and enqueues it for the next SyncClient.push()
  Future<void> _enqueueUpsert(String id) async {
    final row = await (_db.select(_db.projects)..where((t) => t.id.equals(id))).getSingleOrNull();
    if (row == null) return;
    await _db.enqueueOutbox(table: 'projects', recordId: id, operation: 'upsert', payload: SyncPayloads.project(row));
  }

  /// Watch all projects
  Stream<List<Project>> watchProjects() {
    return (_db.select(_db.projects)
          ..orderBy([
            (t) => OrderingTerm(expression: t.status, mode: OrderingMode.asc),
            (t) => OrderingTerm(expression: t.targetDate, mode: OrderingMode.asc),
            (t) => OrderingTerm(expression: t.createdAt, mode: OrderingMode.desc),
          ]))
        .watch();
  }

  /// Insert a project
  Future<void> addProject({
    required String title,
    String? description,
    required String status,
    DateTime? targetDate,
  }) async {
    final id = _uuid.v4();
    final project = ProjectsCompanion.insert(
      id: id,
      title: title,
      description: Value(description),
      status: status,
      targetDate: Value(targetDate),
      createdAt: DateTime.now(),
      isSynced: const Value(false),
      updatedAt: Value(DateTime.now()),
    );
    await _db.into(_db.projects).insert(project);
    await _enqueueUpsert(id);
  }

  /// Update project status
  Future<void> updateProjectStatus(String id, String status) async {
    await (_db.update(_db.projects)..where((t) => t.id.equals(id))).write(
      ProjectsCompanion(
        status: Value(status),
        isSynced: const Value(false),
        updatedAt: Value(DateTime.now()),
      ),
    );
    await _enqueueUpsert(id);
  }

  /// Update project fully
  Future<void> updateProject(Project project) async {
    final updated = project.copyWith(
      isSynced: false,
      updatedAt: Value(DateTime.now()),
    );
    await _db.into(_db.projects).insertOnConflictUpdate(updated);
    await _enqueueUpsert(project.id);
  }

  /// Delete a project
  Future<void> deleteProject(String id) async {
    await (_db.delete(_db.projects)..where((t) => t.id.equals(id))).go();
    await _db.enqueueOutbox(table: 'projects', recordId: id, operation: 'delete');
  }
}

final projectRepositoryProvider = Provider<ProjectRepository>((ref) {
  final db = ref.watch(databaseProvider);
  return ProjectRepository(db);
});