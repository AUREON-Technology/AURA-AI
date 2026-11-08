/// Two writes to the same record on the same clock tick.
///
/// `enqueueOutbox` used to key each row as `table_recordId_<microseconds>`. On
/// Windows, back-to-back `DateTime.now()` calls can return the same microsecond,
/// so saving a memory and forgetting it straight away gave both outbox rows the
/// same primary key: the second insert threw `UNIQUE constraint failed:
/// outbox_entries.id` and that change never reached the outbox. Client CI's
/// Windows job hit it in `remote_deletes_test.dart` on 2026-11-08.
///
/// The same tie left push order unguaranteed. `createdAt` is stored in whole
/// seconds, so a save and a delete within one second sort equal, and SQL leaves
/// the order of ties undefined. SQLite returns them in insertion order today,
/// so the order tests below pass without the `rowid` tie-break too; they pin
/// the behaviour the tie-break now guarantees.
///
/// [AppDatabase.outboxClock] pins every write to one instant, which reproduces
/// the Windows tick on any platform.
library;

import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/core/sync/sync_client.dart';
import 'package:drift/native.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

const _record = '9f1c2d3e-0000-4000-8000-000000000001';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    final tick = DateTime.utc(2026, 11, 8, 13, 4, 24, 350, 468);
    db.outboxClock = () => tick;
  });

  tearDown(() async {
    await db.close();
  });

  test('two writes to one record on the same tick are both queued', () async {
    await db.enqueueOutbox(table: 'memories', recordId: _record, operation: 'upsert', payload: {'fact_enc': 'x'});
    await db.enqueueOutbox(table: 'memories', recordId: _record, operation: 'delete');

    final pending = await db.pendingOutbox();
    expect(pending, hasLength(2));
    expect(pending.map((e) => e.id).toSet(), hasLength(2), reason: 'each row needs its own key');
  });

  test('rows written on the same tick come out in the order they were written', () async {
    // Enough rows, in an order that is neither alphabetical by operation nor
    // by record, that a correct result cannot be luck.
    const writes = [
      ('upsert', 'r3'),
      ('delete', 'r1'),
      ('upsert', 'r1'),
      ('delete', 'r3'),
      ('upsert', 'r2'),
      ('delete', 'r2'),
    ];
    for (final (op, id) in writes) {
      await db.enqueueOutbox(table: 'tasks', recordId: id, operation: op);
    }

    final pending = await db.pendingOutbox();
    expect(
      pending.map((e) => (e.operation, e.recordId)).toList(),
      writes,
    );
  });

  test('a save and the delete that undoes it are pushed in that order', () async {
    await db.enqueueOutbox(table: 'memories', recordId: _record, operation: 'upsert', payload: {'fact_enc': 'x'});
    await db.enqueueOutbox(table: 'memories', recordId: _record, operation: 'delete');

    List<dynamic>? sent;
    final client = MockClient((request) async {
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      sent = body['mutations'] as List<dynamic>;
      return http.Response(
        jsonEncode([
          for (final m in sent!) {'id': (m as Map)['id'], 'table': m['table'], 'status': 'applied'},
        ]),
        200,
      );
    });
    final container = ProviderContainer(
      overrides: [
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        syncClientProvider.overrideWith((ref) => SyncClient(db, ref, httpClient: client)),
      ],
    );
    addTearDown(container.dispose);

    final result = await container.read(syncClientProvider).push();

    expect(result.ok, isTrue);
    expect(sent!.map((m) => (m as Map)['op']).toList(), ['upsert', 'delete']);
    expect(await db.pendingOutbox(), isEmpty);
  });
}
