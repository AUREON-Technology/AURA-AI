// A server table the client does not sync yet must not break a pull.
//
// The backend added `conversations` (migration 0034: title, pin and archive state for a chat) to
// the cursor sync before the app has a Drift table or a screen for it. Every installed app build
// meets that key in the pull's `data` on its next sync. The client reads pulled tables by name
// (`rows("tasks")`, `rows("messages")`, ...), so an unknown name should be ignored; this proves it
// instead of leaving it as a reading of `sync_client.dart`: the known rows in the same page still
// apply, the pull succeeds, and the cursor advances (a pull that threw here would stall that
// device's sync, in both directions, for good).
import 'dart:convert';

import 'package:aura_client/core/auth/refresh_token_store.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/core/sync/sync_client.dart';
import 'package:drift/native.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _MemoryStore implements RefreshTokenStore {
  String? token;

  @override
  Future<String?> read() async => token;

  @override
  Future<void> write(String value) async => token = value;

  @override
  Future<void> delete() async => token = null;
}

class _SignedInTokens extends TokenManager {
  _SignedInTokens(super.ref) : super(store: _MemoryStore());

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
  });

  tearDown(() => db.close());

  Map<String, dynamic> pageWithAnUnknownTable() => {
        'cursor': 'txid:9',
        'data': {
          'finance_records': [
            {
              'id': 'ffffffff-ffff-ffff-ffff-ffffffffffff',
              'type': 'expense',
              'amount': 12.5,
              'category': 'food',
              'description_enc': null,
              'occurred_at': '2026-11-22T10:00:00Z',
              'created_at': '2026-11-22T10:00:00Z',
              'updated_at': '2026-11-22T10:00:00Z',
              'deleted_at': null,
            },
          ],
          'conversations': [
            {
              'id': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
              'conversation_key': 'chat-1',
              'title_enc': 'gcm2:iv:body',
              'pinned': true,
              'archived': false,
              'created_at': '2026-11-22T10:00:00Z',
              'updated_at': '2026-11-22T10:00:00Z',
              'deleted_at': null,
            },
          ],
          // A tombstone for a table the app has never heard of must be as harmless.
          'some_future_table': [
            {'id': 'dddddddd-dddd-dddd-dddd-dddddddddddd', 'deleted_at': '2026-11-22T10:00:00Z'},
          ],
        },
      };

  test('a pull carrying tables this build does not know still applies the ones it does', () async {
    final server = MockClient((_) async => http.Response(jsonEncode(pageWithAnUnknownTable()), 200));
    final container = ProviderContainer(overrides: [
      tokenManagerProvider.overrideWith((ref) => _SignedInTokens(ref)),
      syncClientProvider.overrideWith((ref) => SyncClient(db, ref, httpClient: server)),
    ]);
    addTearDown(container.dispose);

    final result = await container.read(syncClientProvider).pull();

    expect(result.ok, isTrue, reason: 'an unknown table must not fail the pull');
    expect(await db.select(db.financeRecords).get(), hasLength(1),
        reason: 'the known rows in the same page still land');
    expect(await db.getSyncCursor(), 'txid:9', reason: 'the cursor advances, or the device re-pulls forever');
  });
}
