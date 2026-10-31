// ADR-032: the account data key (gcm2), its compatibility with the server,
// and the move off the per-device key.
import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/security/data_key_migration.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/core/sync/sync_client.dart';
import 'package:drift/drift.dart' hide isNull, isNotNull;
import 'package:drift/native.dart';
import 'package:encrypt/encrypt.dart' as encrypt;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// The server's fixed vector (aura_backend/tests/unit/test_user_data_keys.py):
/// key bytes 0..31, IV bytes 100..111, this kid and plaintext.
final _fixedKey = encrypt.Key(Uint8List.fromList(List.generate(32, (i) => i)));
final _fixedIv = encrypt.IV(Uint8List.fromList(List.generate(12, (i) => 100 + i)));
const _kid = '0a1b2c3d';
const _vector =
    'gcm2:0a1b2c3d:ZGVmZ2hpamtsbW5v:Cm6nRhSAOvUegN98+rzvJfMaq9IjtFTMqQ5qMV+mBS4tZv5K706K';
const _plaintext = 'Buy milk — مرحبا';

final _legacy = encrypt.Key.fromSecureRandom(32);
final _dek = encrypt.Key.fromSecureRandom(32);
const _dekKid = 'abcdef01';

void useLegacyOnly() => EncryptionService.debugSetKeysForTesting(legacy: _legacy);
void useBoth() => EncryptionService.debugSetKeysForTesting(
      legacy: _legacy, dataKeys: {_dekKid: _dek}, primaryKid: _dekKid);

/// Ciphertext another device wrote with a key this one never had.
String foreignCiphertext(String text) {
  EncryptionService.debugSetKeysForTesting(legacy: encrypt.Key.fromSecureRandom(32));
  final out = EncryptionService.encryptText(text);
  useLegacyOnly();
  return out;
}

/// What the server writes for this account (gcm2 under the account key).
String serverCiphertext(String text) {
  EncryptionService.debugSetKeysForTesting(dataKeys: {_dekKid: _dek}, primaryKid: _dekKid);
  final out = EncryptionService.encryptText(text);
  useLegacyOnly();
  return out;
}

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  tearDown(() async {
    EncryptionService.debugResetKeyForTesting();
    await EncryptionService.init();
  });

  group('gcm2 format', () {
    test('is byte-compatible with the server in both directions', () {
      expect(
        EncryptionService.encryptGcm2WithIvForTesting(_fixedKey, _kid, _fixedIv, _plaintext),
        _vector,
        reason: 'the server decrypts exactly this string (test_user_data_keys.py)',
      );
      EncryptionService.debugSetKeysForTesting(dataKeys: {_kid: _fixedKey}, primaryKid: _kid);
      expect(EncryptionService.decryptText(_vector), _plaintext);
    });

    test('round-trips under the primary data key and names it', () {
      useBoth();
      final sealed = EncryptionService.encryptText('Dentist at 9');
      expect(sealed, startsWith('gcm2:$_dekKid:'));
      expect(sealed.split(':'), hasLength(4));
      expect(EncryptionService.decryptText(sealed), 'Dentist at 9');
    });

    test('legacy gcm: values stay readable while both keys are held', () {
      useLegacyOnly();
      final old = EncryptionService.encryptText('old note');
      expect(old, startsWith('gcm:'));
      useBoth();
      expect(EncryptionService.decryptText(old), 'old note');
    });

    test('a server row pulled before the key arrived becomes readable once it does', () {
      final server = serverCiphertext('Agent-made task');
      // The pull stored it as text under the device key.
      final local = EncryptionService.encryptText(server);
      expect(EncryptionService.decryptText(local), server);
      useBoth();
      expect(EncryptionService.decryptText(local), 'Agent-made task');
    });

    test('an unknown key id is reported so the keys are fetched again', () {
      useBoth();
      final other = 'gcm2:99999999:${_vector.split(':')[2]}:${_vector.split(':')[3]}';
      expect(EncryptionService.decryptText(other), other);
      expect(EncryptionService.sawUnknownDataKey, isTrue);
    });

    test('malformed key sets are refused and leave the current keys in place', () async {
      useBoth();
      await expectLater(
        EncryptionService.installDataKeys('nope', {'nope': base64Encode(List.filled(16, 1))}),
        throwsFormatException,
      );
      expect(EncryptionService.encryptText('x'), startsWith('gcm2:$_dekKid:'));
    });
  });

  group('key-loss check (beforeOpen wipe)', () {
    test('only a device-key value the device key cannot open counts as loss', () {
      useLegacyOnly();
      expect(EncryptionService.isLocalKeyLoss(EncryptionService.encryptText('mine')), isFalse);
      expect(EncryptionService.isLocalKeyLoss(foreignCiphertext('theirs')), isTrue);
      // Another device's ciphertext inside this device's wrapper: not loss.
      expect(
        EncryptionService.isLocalKeyLoss(EncryptionService.encryptText(foreignCiphertext('x'))),
        isFalse,
      );
      // A gcm2 value whose key is not here yet: refetchable, not loss.
      expect(EncryptionService.isLocalKeyLoss(serverCiphertext('y')), isFalse);
    });
  });

  group('DataKeyMigration', () {
    late AppDatabase db;

    setUp(() {
      useLegacyOnly();
      db = AppDatabase.forTesting(NativeDatabase.memory());
    });

    tearDown(() async => db.close());

    Future<void> insertTask(String id, String title) => db.into(db.tasks).insert(
          TasksCompanion.insert(
            id: id, title: title, priority: 'medium', isCompleted: false,
            category: 'personal', createdAt: DateTime(2026, 10, 1),
          ),
        );

    Future<String> rawTitle(String id) async => (await db
            .customSelect('SELECT title FROM tasks WHERE id = ?', variables: [Variable.withString(id)])
            .getSingle())
        .read<String>('title');

    Future<List<OutboxEntry>> outboxFor(String id) =>
        (db.select(db.outboxEntries)..where((t) => t.recordId.equals(id))).get();

    const mine = '11111111-1111-1111-1111-111111111111';
    const theirs = '22222222-2222-2222-2222-222222222222';
    const fromServer = '33333333-3333-3333-3333-333333333333';
    const second = '44444444-4444-4444-4444-444444444444';

    Future<void> seed() async {
      await insertTask(mine, 'Pay rent');
      await insertTask(second, 'Water plants');
      await insertTask(theirs, foreignCiphertext('Phone task'));
      await insertTask(fromServer, serverCiphertext('Agent task'));
      await db.into(db.messages).insert(MessagesCompanion.insert(
            id: '55555555-5555-5555-5555-555555555555', conversationId: 'c1', role: 'user',
            content: 'hello', createdAt: DateTime(2026, 10, 1),
          ));
    }

    test('a fresh run re-encrypts everything, queues uploads and retires the device key', () async {
      await seed();
      expect(await db.hasUnreadableLocalCiphertext(), isFalse,
          reason: 'another device\'s rows must not look like key loss');
      useBoth();

      final report = await DataKeyMigration(db).run();

      for (final id in [mine, second, theirs, fromServer]) {
        expect(await rawTitle(id), startsWith('gcm2:'));
      }
      final tasks = {for (final t in await db.select(db.tasks).get()) t.id: t.title};
      expect(tasks[mine], 'Pay rent');
      expect(tasks[fromServer], 'Agent task');
      expect(tasks[theirs], startsWith('gcm:'), reason: 'readable once that device migrates');

      final upload = (await outboxFor(mine)).single;
      final payload = jsonDecode(upload.payloadJson!) as Map<String, dynamic>;
      expect(payload['title_enc'], startsWith('gcm2:$_dekKid:'));
      expect(EncryptionService.decryptText(payload['title_enc'] as String), 'Pay rent');
      expect(await outboxFor(theirs), isEmpty, reason: 'another device\'s row is never pushed');
      expect(report.foreign, 1);
      expect(report.remaining, 0);
      expect(report.legacyRetired, isTrue);
      expect(EncryptionService.hasLegacyKey, isFalse);
      expect(await db.hasUnreadableLocalCiphertext(), isFalse);

      final again = await DataKeyMigration(db).run();
      expect(again.rewrapped, 0, reason: 'idempotent');
    });

    test('resumes after being killed partway, without duplicate uploads', () async {
      await seed();
      useBoth();

      final partial = await DataKeyMigration(db).run(stopAfterRows: 2);
      expect(partial.legacyRetired, isFalse);
      expect(EncryptionService.hasLegacyKey, isTrue);
      expect(await DataKeyMigration(db).remainingLegacyValues(), greaterThan(0));
      expect(await db.hasUnreadableLocalCiphertext(), isFalse,
          reason: 'a half-migrated database is not key loss');

      final rest = await DataKeyMigration(db).run();
      expect(rest.legacyRetired, isTrue);
      for (final id in [mine, second]) {
        expect(await outboxFor(id), hasLength(1));
        expect(await rawTitle(id), startsWith('gcm2:'));
      }
    });

    test('queued outbox payloads are re-encrypted before the device key goes', () async {
      await db.enqueueOutbox(
        table: 'tasks', recordId: mine, operation: 'upsert',
        payload: {'title_enc': EncryptionService.encryptText('queued'), 'priority': 'low'},
      );
      useBoth();

      final report = await DataKeyMigration(db).run();

      expect(report.outboxRewrapped, 1);
      final payload =
          jsonDecode((await outboxFor(mine)).single.payloadJson!) as Map<String, dynamic>;
      expect(payload['title_enc'], startsWith('gcm2:'));
      expect(EncryptionService.decryptText(payload['title_enc'] as String), 'queued');
      expect(report.legacyRetired, isTrue);
    });

    test('signing out forgets the account key but keeps a key to write with', () async {
      useBoth();
      await EncryptionService.retireLegacyKey();
      expect(EncryptionService.hasLegacyKey, isFalse);

      await db.wipeUserData();

      expect(EncryptionService.hasDataKey, isFalse);
      expect(EncryptionService.encryptText('next account'), startsWith('gcm:'));
    });

    test('genuine local key loss is still detected and keeps the device key', () async {
      await db.customStatement(
        "INSERT INTO tasks (id, title, priority, is_completed, category, created_at) "
        "VALUES ('66666666-6666-6666-6666-666666666666', ?, 'low', 0, 'x', 0)",
        [foreignCiphertext('lost')],
      );
      expect(await db.hasUnreadableLocalCiphertext(), isTrue);
      useBoth();

      final report = await DataKeyMigration(db).run();

      expect(report.unreadable, 1);
      expect(report.legacyRetired, isFalse);
      expect(EncryptionService.hasLegacyKey, isTrue);
    });
  });

  group('SyncClient', () {
    late AppDatabase db;

    setUp(() {
      useLegacyOnly();
      db = AppDatabase.forTesting(NativeDatabase.memory());
    });

    tearDown(() async => db.close());

    SyncClient clientWith(http.Client httpClient) {
      late SyncClient client;
      final container = ProviderContainer(overrides: [
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        syncClientProvider.overrideWith((ref) {
          client = SyncClient(db, ref, httpClient: httpClient);
          return client;
        }),
      ]);
      addTearDown(container.dispose);
      return container.read(syncClientProvider);
    }

    http.Response pull([Map<String, dynamic> data = const {}]) =>
        http.Response(jsonEncode({'cursor': 'txid:9', 'data': data}), 200);

    test('offline: without the key endpoint the app keeps working on the device key', () async {
      final client = clientWith(MockClient((request) async {
        if (request.url.path == '/api/v3/keys/data') throw http.ClientException('offline');
        return pull();
      }));

      final result = await client.synchronize();

      expect(result.ok, isTrue);
      expect(EncryptionService.hasDataKey, isFalse);
      expect(EncryptionService.encryptText('still works'), startsWith('gcm:'));
    });

    test('a backend without the endpoint yet (503) is treated the same way', () async {
      final client = clientWith(MockClient((request) async {
        if (request.url.path == '/api/v3/keys/data') return http.Response('{}', 503);
        return pull();
      }));

      expect((await client.synchronize()).ok, isTrue);
      expect(EncryptionService.hasDataKey, isFalse);
    });

    test('online: fetches the key, migrates, and pushes gcm2 through the normal outbox', () async {
      await db.into(db.tasks).insert(TasksCompanion.insert(
            id: '77777777-7777-7777-7777-777777777777', title: 'Book flights',
            priority: 'high', isCompleted: false, category: 'travel',
            createdAt: DateTime(2026, 10, 2),
          ));
      final pushed = <Map<String, dynamic>>[];
      final client = clientWith(MockClient((request) async {
        if (request.url.path == '/api/v3/keys/data') {
          expect(request.headers['authorization'], 'Bearer jwt-test');
          return http.Response(jsonEncode({
            'format': 'gcm2', 'primary': _dekKid,
            'keys': [{'kid': _dekKid, 'key': _dek.base64, 'created_at': '2026-10-31T00:00:00Z'}],
          }), 200);
        }
        if (request.method == 'POST') {
          final mutations = (jsonDecode(request.body) as Map<String, dynamic>)['mutations'] as List;
          pushed.addAll(mutations.cast<Map<String, dynamic>>());
          return http.Response(jsonEncode([
            for (final m in mutations) {'id': m['id'], 'table': m['table'], 'status': 'applied'},
          ]), 200);
        }
        return pull();
      }));

      expect((await client.synchronize()).ok, isTrue);

      expect(EncryptionService.hasDataKey, isTrue);
      expect(EncryptionService.hasLegacyKey, isFalse);
      final upload = pushed.single;
      expect(upload['id'], '77777777-7777-7777-7777-777777777777');
      final titleEnc = (upload['payload'] as Map<String, dynamic>)['title_enc'] as String;
      expect(titleEnc, startsWith('gcm2:$_dekKid:'));
      expect(await db.select(db.outboxEntries).get(), isEmpty);
      expect((await db.select(db.tasks).getSingle()).title, 'Book flights');
    });

    test('a row the server wrote decrypts on pull', () async {
      useBoth();
      final client = clientWith(MockClient((request) async => pull({
            'tasks': [
              {
                'id': '88888888-8888-8888-8888-888888888888',
                'title_enc': serverCiphertextWithBoth('Renew passport'),
                'description_enc': null, 'priority': 'high', 'category': null,
                'due_date': null, 'completed_at': null, 'google_task_id': null,
                'created_at': '2026-10-31T00:00:00+00:00', 'updated_at': '2026-10-31T00:00:00+00:00',
                'deleted_at': null,
              },
            ],
          })));

      expect((await client.pull()).ok, isTrue);
      expect((await db.select(db.tasks).getSingle()).title, 'Renew passport');
    });
  });
}

String serverCiphertextWithBoth(String text) {
  useBoth();
  return EncryptionService.encryptText(text);
}
