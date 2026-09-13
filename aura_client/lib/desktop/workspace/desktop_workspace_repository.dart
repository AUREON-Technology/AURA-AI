/// Workspace repository for Desktop IDE.
///
/// CRUD operations on DesktopWorkspaces table.
library;

import 'package:drift/drift.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';

class DesktopWorkspaceRepository {
  DesktopWorkspaceRepository(this._db);

  final AppDatabase _db;

  Future<List<DesktopWorkspace>> getAll() async {
    final workspaces = await _db.select(_db.desktopWorkspaces).get();
    return workspaces.map(DesktopWorkspace.fromDb).toList();
  }

  Future<DesktopWorkspace?> getById(String id) async {
    final workspace = await (_db.select(_db.desktopWorkspaces)..where((t) => t.id.equals(id))).getSingleOrNull();
    return workspace != null ? DesktopWorkspace.fromDb(workspace) : null;
  }

  Future<void> create({
    required String id,
    required String rootPath,
    required String displayName,
  }) async {
    await _db.into(_db.desktopWorkspaces).insert(
      DesktopWorkspacesCompanion.insert(
        id: id,
        rootPath: rootPath,
        displayName: displayName,
        lastOpenedAt: DateTime.now(),
      ),
    );
  }

  Future<void> updateLastOpened(String id) async {
    await (_db.update(_db.desktopWorkspaces)..where((t) => t.id.equals(id))).write(
      DesktopWorkspacesCompanion(lastOpenedAt: Value(DateTime.now())),
    );
  }

  Future<void> delete(String id) async {
    await _db.delete(_db.desktopWorkspaces).where((t) => t.id.equals(id)).go();
  }

  Future<void> deleteAll() async {
    await _db.delete(_db.desktopWorkspaces).go();
  }
}