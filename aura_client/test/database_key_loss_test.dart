// F3: the key-loss wipe in `AppDatabase.beforeOpen` cleared the content tables but kept the sync
// cursor, so every later pull was incremental from that cursor and the cleared history never came
// back. Only the cursor goes now; the outbox and the other `sync_metadata` keys stay.
import 'dart:io';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:drift/native.dart';
import 'package:encrypt/encrypt.dart' as encrypt;
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late Directory dir;
  late File file;

  setUp(() async {
    dir = await Directory.systemTemp.createTemp('aura_key_loss_');
    file = File('${dir.path}/app.sqlite');
  });

  tearDown(() async {
    EncryptionService.debugResetKeyForTesting();
    await dir.delete(recursive: true);
  });

  test('a wipe after key loss forgets the sync cursor but keeps the outbox', () async {
    final deviceKey = encrypt.Key.fromSecureRandom(32);
    EncryptionService.debugSetKeysForTesting(legacy: deviceKey);

    // Before: a synced device with a cursor, an unsent change, and a notification watermark.
    var db = AppDatabase.forTesting(NativeDatabase(file));
    await db.setSyncCursor('cursor-42');
    await db.setNotificationsLastRead(DateTime.utc(2026, 11, 1));
    await db.enqueueOutbox(
      table: 'tasks',
      recordId: '11111111-1111-1111-1111-111111111111',
      operation: 'upsert',
      payload: const {'priority': 'low'},
    );
    // A row sealed with a device key this device no longer has (a restore onto new hardware).
    EncryptionService.debugSetKeysForTesting(legacy: encrypt.Key.fromSecureRandom(32));
    final lost = EncryptionService.encryptText('Pay rent');
    EncryptionService.debugSetKeysForTesting(legacy: deviceKey);
    await db.customStatement(
      "INSERT INTO tasks (id, title, priority, is_completed, category, created_at, is_synced) "
      "VALUES ('t1', ?, 'medium', 0, 'personal', 0, 1)",
      [lost],
    );
    await db.close();

    // Reopen: beforeOpen finds the unreadable row and wipes.
    db = AppDatabase.forTesting(NativeDatabase(file));
    addTearDown(db.close);

    expect(await db.select(db.tasks).get(), isEmpty, reason: 'unreadable content is cleared');
    expect(await db.getSyncCursor(), isNull,
        reason: 'the next pull must be a full one, or the cleared history never returns');
    expect(await db.select(db.outboxEntries).get(), hasLength(1),
        reason: 'unsynced changes are not destroyed');
    expect((await db.getNotificationsLastRead())?.isAtSameMomentAs(DateTime.utc(2026, 11, 1)), isTrue,
        reason: 'the notifications watermark is not part of the cleared content');
  });
}
