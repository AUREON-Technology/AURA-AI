// F24: Google Tasks sync changed local tasks without queueing them for the AURA backend, so an
// imported, updated or removed task, and the link to its Google copy, never reached the server.
import 'dart:convert';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/tasks/data/task_repository.dart';
import 'package:drift/drift.dart' hide isNull, isNotNull;
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:googleapis/tasks/v1.dart' as tasks_api;

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late TaskRepository repo;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    repo = TaskRepository(db);
  });

  tearDown(() => db.close());

  Future<List<(String, String)>> queued() async => [
        for (final e in await db.select(db.outboxEntries).get()) (e.recordId, e.operation),
      ];

  Future<Task> byGoogleId(String id) =>
      (db.select(db.tasks)..where((t) => t.googleTaskId.equals(id))).getSingle();

  Future<void> insertLinked(String id, String googleId, {required DateTime updated}) =>
      db.into(db.tasks).insert(TasksCompanion.insert(
            id: id,
            title: 'old title',
            priority: 'medium',
            category: 'personal',
            isCompleted: false,
            createdAt: DateTime.utc(2026, 11, 1),
            googleTaskId: Value(googleId),
            isSynced: const Value(true),
            updatedAt: Value(updated),
          ));

  test('an import, a newer remote edit and a remote delete are all queued for the backend', () async {
    await insertLinked('11111111-1111-1111-1111-111111111111', 'g-edited', updated: DateTime.utc(2026, 11, 1));
    await insertLinked('22222222-2222-2222-2222-222222222222', 'g-gone', updated: DateTime.utc(2026, 11, 1));

    await repo.reconcileGoogleTasks(
      [
        tasks_api.Task(id: 'g-new', title: 'From Google', updated: '2026-11-18T08:00:00Z'),
        tasks_api.Task(id: 'g-edited', title: 'Edited on Google', updated: '2026-11-18T09:00:00Z'),
        tasks_api.Task(id: 'g-gone', title: 'old title', deleted: true),
      ],
      logs: [],
      insertRemote: (_) async => fail('nothing local to upload'),
      updateRemote: (_, _) async => fail('nothing local modified'),
    );

    final imported = await byGoogleId('g-new');
    expect(
      await queued(),
      unorderedEquals([
        (imported.id, 'upsert'),
        ('11111111-1111-1111-1111-111111111111', 'upsert'),
        ('22222222-2222-2222-2222-222222222222', 'delete'),
      ]),
    );
    final edited = (await db.select(db.outboxEntries).get())
        .firstWhere((e) => e.recordId == '11111111-1111-1111-1111-111111111111');
    expect(EncryptionService.decryptText((jsonDecode(edited.payloadJson!) as Map)['title_enc'] as String),
        'Edited on Google');
  });

  test('the Google id given to an uploaded task is queued with it', () async {
    await repo.addTask(title: 'Local', priority: 'low', category: 'personal');
    await db.delete(db.outboxEntries).go(); // the add itself was already queued

    await repo.reconcileGoogleTasks(
      const [],
      logs: [],
      insertRemote: (_) async => 'g-created',
      updateRemote: (_, _) async {},
    );

    final task = await byGoogleId('g-created');
    final entry = (await db.select(db.outboxEntries).get()).single;
    expect(entry.recordId, task.id);
    expect((jsonDecode(entry.payloadJson!) as Map)['google_task_id'], 'g-created');
  });

  test('mock sync imports are queued too', () async {
    await repo.syncGoogleTasks(isMock: true);
    final tasks = await db.select(db.tasks).get();
    expect(tasks, hasLength(2));
    expect(await queued(), unorderedEquals([for (final t in tasks) (t.id, 'upsert')]));
  });
}
