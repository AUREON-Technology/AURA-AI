// F4: a health day is a calendar date, stored as its UTC midnight -- the instant the server's own
// tools write. The client keyed it on local midnight, so east of Greenwich the assistant's entry
// for today (`…T00:00Z`) and the user's (`…T22:00Z` the day before) never met.
//
// The helper and migration tests pass an explicit offset and hold in any time zone. The
// repository/pull test exercises the device's own zone: run it under `TZ=Africa/Cairo` too (it
// is meaningful anywhere east of UTC; in UTC itself the two conventions coincide).
import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/database/health_day.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/core/sync/sync_client.dart';
import 'package:aura_client/core/sync/sync_payloads.dart';
import 'package:aura_client/features/health/data/health_repository.dart';
import 'package:drift/drift.dart' hide isNull, isNotNull;
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

const _cairo = Duration(hours: 2);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('day keys', () {
    test('UTC midnight from the server names its own date', () {
      expect(healthDayOfInstant(DateTime.utc(2026, 11, 18), localOffset: _cairo), DateTime.utc(2026, 11, 18));
      expect(healthDayOfInstant(DateTime.utc(2026, 11, 18), localOffset: const Duration(hours: -5)),
          DateTime.utc(2026, 11, 18),
          reason: 'never shifted to the previous evening west of Greenwich');
    });

    test('a pre-F4 local-midnight key names the local date it was written on', () {
      // Cairo's midnight on the 18th is 22:00Z on the 17th.
      expect(healthDayOfInstant(DateTime.utc(2026, 11, 17, 22), localOffset: _cairo), DateTime.utc(2026, 11, 18));
      // New York's midnight on the 18th is 05:00Z on the 18th.
      expect(healthDayOfInstant(DateTime.utc(2026, 11, 18, 5), localOffset: const Duration(hours: -5)),
          DateTime.utc(2026, 11, 18));
    });

    test('today is the local calendar date, as UTC midnight', () {
      final key = healthDayFor(DateTime(2026, 11, 18, 0, 30));
      expect(key.isUtc, isTrue);
      expect((key.year, key.month, key.day), (2026, 11, 18));
    });

    test('the payload carries UTC midnight of the calendar date', () {
      final payload = SyncPayloads.healthRecord(HealthRecord(
        id: 'h',
        recordDate: healthDayForDisplay(DateTime.utc(2026, 11, 18)),
        waterIntakeMl: 0,
        sleepMinutes: 0,
        caloriesConsumed: 0,
        exerciseMinutes: 0,
        createdAt: DateTime.now(),
        isSynced: false,
      ));
      expect(payload['record_date'], '2026-11-18T00:00:00.000Z');
    });
  });

  group('device time zone', () {
    late AppDatabase db;

    setUp(() async {
      await EncryptionService.init();
      db = AppDatabase.forTesting(NativeDatabase.memory());
    });

    tearDown(() => db.close());

    test('the assistant\'s row for today shows as today, and a user log updates it', () async {
      // The server's row for the 18th, as its agent tools write it.
      final server = MockClient((request) async => http.Response(
            jsonEncode({
              'cursor': 'txid:9',
              'data': {
                'health_records': [
                  {
                    'id': 'server-18',
                    'record_date': '2026-11-18T00:00:00+00:00',
                    'weight': null,
                    'water_ml': 0,
                    'sleep_minutes': 420,
                    'calories': 0,
                    'exercise_minutes': 0,
                    'created_at': '2026-11-18T06:00:00Z',
                    'updated_at': '2026-11-18T06:00:00Z',
                    'deleted_at': null,
                  },
                ],
              },
            }),
            200,
          ));
      final container = ProviderContainer(overrides: [
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        databaseProvider.overrideWithValue(db),
        syncClientProvider.overrideWith((ref) => SyncClient(db, ref, httpClient: server)),
      ]);
      addTearDown(container.dispose);

      expect((await container.read(syncClientProvider).pull()).ok, isTrue);

      // Half past midnight local time on the 18th: still the 17th in UTC east of Greenwich.
      final now = DateTime(2026, 11, 18, 0, 30);
      final repo = container.read(healthRepositoryProvider);
      final today = await repo.watchDailyRecord(now).first;
      expect(today?.id, 'server-18');
      expect(today?.sleepMinutes, 420);
      expect((today!.recordDate.year, today.recordDate.month, today.recordDate.day), (2026, 11, 18),
          reason: 'shown on its own date in every zone');

      await repo.logWater(now, 250);

      final rows = await db.select(db.healthRecords).get();
      expect(rows.map((r) => (r.id, r.waterIntakeMl, r.sleepMinutes)), [('server-18', 250, 420)],
          reason: 'the same day, not a second row');
      final queued = (await db.select(db.outboxEntries).get()).single;
      expect(queued.recordId, 'server-18');
      expect((jsonDecode(queued.payloadJson!) as Map)['record_date'], '2026-11-18T00:00:00.000Z');
    });
  });

  group('migration of pre-F4 rows', () {
    late AppDatabase db;

    setUp(() async {
      await EncryptionService.init();
      db = AppDatabase.forTesting(NativeDatabase.memory());
    });

    tearDown(() => db.close());

    Future<void> insert(String id, DateTime day, {int water = 0, int sleep = 0, double? weight, DateTime? updated}) =>
        db.into(db.healthRecords).insert(HealthRecordsCompanion.insert(
              id: id,
              recordDate: day,
              waterIntakeMl: water,
              sleepMinutes: sleep,
              caloriesConsumed: 0,
              exerciseMinutes: 0,
              weight: Value(weight),
              createdAt: DateTime.utc(2026, 11, 1),
              isSynced: const Value(true),
              updatedAt: Value(updated),
            ));

    test('re-keys local-midnight rows and merges a day that ended up with two', () async {
      // Cairo: the user's own row for the 18th (local midnight) and the assistant's (UTC
      // midnight), plus a lone user row for the 17th.
      await insert('mine-18', DateTime.utc(2026, 11, 17, 22), water: 500, weight: 71.5,
          updated: DateTime.utc(2026, 11, 18, 9));
      await insert('agent-18', DateTime.utc(2026, 11, 18), sleep: 420, updated: DateTime.utc(2026, 11, 18, 6));
      await insert('mine-17', DateTime.utc(2026, 11, 16, 22), water: 300);

      await db.rekeyHealthDays(localOffset: _cairo);

      final rows = {for (final r in await db.select(db.healthRecords).get()) r.id: r};
      expect(rows.keys, unorderedEquals(['agent-18', 'mine-17']),
          reason: 'the server\'s row for the day keeps the day');
      expect(rows['agent-18']!.recordDate.isAtSameMomentAs(DateTime.utc(2026, 11, 18)), isTrue);
      expect(rows['agent-18']!.waterIntakeMl, 500);
      expect(rows['agent-18']!.sleepMinutes, 420);
      expect(rows['agent-18']!.weight, 71.5);
      expect(rows['mine-17']!.recordDate.isAtSameMomentAs(DateTime.utc(2026, 11, 17)), isTrue);

      final outbox = await db.select(db.outboxEntries).get();
      expect(
        outbox.map((e) => (e.recordId, e.operation)),
        unorderedEquals([('mine-18', 'delete'), ('agent-18', 'upsert'), ('mine-17', 'upsert')]),
        reason: 'the server gets the same days: the merged-away row deleted, the others re-keyed',
      );

      await db.rekeyHealthDays(localOffset: _cairo);
      expect(await db.select(db.outboxEntries).get(), hasLength(3), reason: 'idempotent');
    });
  });
}
