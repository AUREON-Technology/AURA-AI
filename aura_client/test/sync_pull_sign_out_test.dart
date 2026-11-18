// F56: a pull still in flight when the user signs out must not write its rows into the database
// sign-out just wiped (the next account would inherit them), nor store its cursor.
import 'dart:async';
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

  Map<String, dynamic> page() => {
        'cursor': 'txid:5',
        'data': {
          'finance_records': [
            {
              'id': 'ffffffff-ffff-ffff-ffff-ffffffffffff',
              'type': 'expense',
              'amount': 12.5,
              'category': 'food',
              'description_enc': null,
              'occurred_at': '2026-11-18T10:00:00Z',
              'created_at': '2026-11-18T10:00:00Z',
              'updated_at': '2026-11-18T10:00:00Z',
              'deleted_at': null,
            },
          ],
        },
      };

  test('a pull answered after sign-out writes nothing', () async {
    final answer = Completer<http.Response>();
    final requested = Completer<void>();
    final server = MockClient((request) {
      requested.complete();
      return answer.future;
    });
    final container = ProviderContainer(overrides: [
      tokenManagerProvider.overrideWith((ref) => _SignedInTokens(ref)),
      syncClientProvider.overrideWith((ref) => SyncClient(db, ref, httpClient: server)),
    ]);
    addTearDown(container.dispose);

    final pulling = container.read(syncClientProvider).pull();
    await requested.future;
    // Sign-out: the session ends (and the local data is wiped) while the request is out.
    await container.read(tokenManagerProvider).clear();
    answer.complete(http.Response(jsonEncode(page()), 200));
    final result = await pulling;

    expect(result.ok, isFalse);
    expect(await db.select(db.financeRecords).get(), isEmpty,
        reason: 'the signed-out account\'s rows must not land in the wiped database');
    expect(await db.getSyncCursor(), isNull);
  });

  test('a pull within one session still applies', () async {
    final server = MockClient((_) async => http.Response(jsonEncode(page()), 200));
    final container = ProviderContainer(overrides: [
      tokenManagerProvider.overrideWith((ref) => _SignedInTokens(ref)),
      syncClientProvider.overrideWith((ref) => SyncClient(db, ref, httpClient: server)),
    ]);
    addTearDown(container.dispose);

    expect((await container.read(syncClientProvider).pull()).ok, isTrue);
    expect(await db.select(db.financeRecords).get(), hasLength(1));
    expect(await db.getSyncCursor(), 'txid:5');
  });
}
