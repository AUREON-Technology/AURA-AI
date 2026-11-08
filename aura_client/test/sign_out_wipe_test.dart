import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';

/// Sign-out used to clear only the tokens. Local tables have no user
/// column and the outbox and cursor are global, so the next account to sign
/// in on the device saw the previous one's data, pushed its unsynced rows
/// as its own, and pulled from its cursor.
void main() {
  late AppDatabase db;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
  });

  tearDown(() => db.close());

  test('wipeUserData removes account data, the outbox and the sync cursor', () async {
    await db.into(db.tasks).insert(TasksCompanion.insert(
          id: 't1',
          title: 'Private task',
          priority: 'low',
          isCompleted: false,
          category: 'personal',
          createdAt: DateTime.now(),
        ));
    await db.enqueueOutbox(table: 'tasks', recordId: 't1', operation: 'upsert', payload: {});
    await db.setSyncCursor('2026-10-01T00:00:00Z');

    await db.wipeUserData();

    expect(await db.select(db.tasks).get(), isEmpty);
    expect(await db.select(db.outboxEntries).get(), isEmpty);
    expect(await db.getSyncCursor(), isNull);
  });

  test('wipeUserData removes unsaved editor buffers (hot exit)', () async {
    await db.into(db.desktopUnsavedBuffers).insert(DesktopUnsavedBuffersCompanion.insert(
          workspaceId: 'ws-1',
          tabId: 'tab-1',
          path: 'lib/main.dart',
          contentEnc: EncryptionService.encryptText('unsaved'),
          updatedAt: DateTime.now(),
        ));

    await db.wipeUserData();

    expect(await db.select(db.desktopUnsavedBuffers).get(), isEmpty);
  });

  test('wipeUserData keeps device-level data such as downloaded model weights', () async {
    await db.into(db.offlineModelWeights).insert(OfflineModelWeightsCompanion.insert(
          id: 'phi-3',
          modelName: 'Phi-3',
          sizeBytes: '1000',
          updatedAt: DateTime.now(),
        ));

    await db.wipeUserData();

    expect(await db.select(db.offlineModelWeights).get(), hasLength(1));
  });
}
