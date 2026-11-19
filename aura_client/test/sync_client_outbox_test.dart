import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/core/sync/sync_client.dart';
import 'package:aura_client/core/sync/sync_result.dart';
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

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
  });

  tearDown(() async {
    await db.close();
  });

  ProviderContainer containerWith(http.Client httpClient) {
    late SyncClient client;
    final container = ProviderContainer(
      overrides: [
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        syncClientProvider.overrideWith((ref) {
          client = SyncClient(db, ref, httpClient: httpClient);
          return client;
        }),
      ],
    );
    addTearDown(container.dispose);
    return container;
  }

  test('push keeps error outbox rows and drops applied/conflicted ones', () async {
    await db.enqueueOutbox(
      table: 'messages',
      recordId: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      operation: 'upsert',
      payload: {'role': 'user'},
    );
    await db.enqueueOutbox(
      table: 'messages',
      recordId: 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
      operation: 'upsert',
      payload: {'role': 'assistant'},
    );
    await db.enqueueOutbox(
      table: 'tasks',
      recordId: 'cccccccc-cccc-cccc-cccc-cccccccccccc',
      operation: 'delete',
    );

    final httpClient = MockClient((request) async {
      expect(request.url.path, '/api/v3/sync');
      final body = jsonDecode(request.body) as Map<String, dynamic>;
      final mutations = body['mutations'] as List;
      return http.Response(
        jsonEncode([
          {'id': mutations[0]['id'], 'table': 'messages', 'status': 'applied'},
          {
            'id': mutations[1]['id'],
            'table': 'messages',
            'status': 'error',
            'detail': 'unknown table',
          },
          {'id': mutations[2]['id'], 'table': 'tasks', 'status': 'conflict'},
        ]),
        200,
      );
    });

    final result = await containerWith(httpClient).read(syncClientProvider).push();
    expect(result.ok, isTrue);

    final remaining = await db.select(db.outboxEntries).get();
    expect(remaining, hasLength(1));
    expect(remaining.single.recordId, 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb');
    expect(remaining.single.targetTable, 'messages');
  });

  Map<String, dynamic> emptyPull([String cursor = 'txid:1']) => {
        'cursor': cursor,
        'data': <String, dynamic>{},
      };

  test('synchronize still pulls when the push fails (F-11)', () async {
    await db.enqueueOutbox(
      table: 'tasks',
      recordId: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      operation: 'delete',
    );
    final methods = <String>[];
    final httpClient = MockClient((request) async {
      // Only the sync endpoint: the data-key fetch after the pull (ADR-032) is not under test.
      if (request.url.path == '/api/v3/sync') methods.add(request.method);
      if (request.method == 'POST') return http.Response('boom', 500);
      return http.Response(jsonEncode(emptyPull('txid:42')), 200);
    });

    final result = await containerWith(httpClient).read(syncClientProvider).synchronize();

    expect(methods, ['POST', 'GET']);
    expect(result.ok, isFalse); // the push failure is still reported...
    expect(await db.getSyncCursor(), 'txid:42'); // ...but the pull ran and landed
    // One row, refused as a whole: nothing says the row rather than the server
    // is at fault, so it is not charged an attempt.
    expect((await db.select(db.outboxEntries).getSingle()).attempts, 0);
  });

  test('a batch refused as a whole is retried row by row; only the bad row is charged', () async {
    const good1 = '11111111-1111-1111-1111-111111111111';
    const bad = '22222222-2222-2222-2222-222222222222';
    const good2 = '33333333-3333-3333-3333-333333333333';
    for (final id in [good1, bad, good2]) {
      await db.enqueueOutbox(table: 'tasks', recordId: id, operation: 'delete');
      await Future<void>.delayed(const Duration(milliseconds: 2));
    }
    final httpClient = MockClient((request) async {
      final mutations = (jsonDecode(request.body) as Map<String, dynamic>)['mutations'] as List;
      // An older server: one bad row fails the whole request.
      if (mutations.any((m) => m['id'] == bad)) return http.Response('boom', 500);
      return http.Response(
        jsonEncode([for (final m in mutations) {'id': m['id'], 'table': 'tasks', 'status': 'applied'}]),
        200,
      );
    });

    final result = await containerWith(httpClient).read(syncClientProvider).push();

    expect(result.ok, isTrue);
    final remaining = await db.select(db.outboxEntries).get();
    expect(remaining.map((e) => e.recordId), [bad]);
    expect(remaining.single.attempts, 1);
    expect(remaining.single.lastError, 'HTTP 500');
  });

  test('a non-retryable row is parked at once and no longer sent', () async {
    const bad = '44444444-4444-4444-4444-444444444444';
    await db.enqueueOutbox(table: 'tasks', recordId: bad, operation: 'delete');
    var posts = 0;
    final httpClient = MockClient((request) async {
      posts++;
      final mutations = (jsonDecode(request.body) as Map<String, dynamic>)['mutations'] as List;
      return http.Response(
        jsonEncode([
          for (final m in mutations)
            {'id': m['id'], 'table': 'tasks', 'status': 'error', 'detail': 'invalid value in payload', 'retryable': false},
        ]),
        200,
      );
    });
    final client = containerWith(httpClient).read(syncClientProvider);

    await client.push();
    final parked = await db.select(db.outboxEntries).getSingle();
    expect(parked.attempts, kMaxOutboxAttempts);
    expect(await db.watchParkedOutboxCount().first, 1);

    await client.push();
    expect(posts, 1, reason: 'a parked row must not be resent on every sync');
  });

  test('a retryable row is parked after kMaxOutboxAttempts failures', () async {
    await db.enqueueOutbox(table: 'goals', recordId: '55555555-5555-5555-5555-555555555555', operation: 'delete');
    final httpClient = MockClient((request) async {
      final mutations = (jsonDecode(request.body) as Map<String, dynamic>)['mutations'] as List;
      return http.Response(
        jsonEncode([
          for (final m in mutations)
            {'id': m['id'], 'table': 'goals', 'status': 'error', 'detail': 'violates a database constraint', 'retryable': true},
        ]),
        200,
      );
    });
    final client = containerWith(httpClient).read(syncClientProvider);

    for (var i = 0; i < kMaxOutboxAttempts; i++) {
      expect(await db.watchParkedOutboxCount().first, 0);
      await client.push();
    }
    expect(await db.watchParkedOutboxCount().first, 1);
    expect(await db.pendingOutbox(), isEmpty);
  });

  test('a conflict result applies the server row it carries', () async {
    const id = '66666666-6666-6666-6666-666666666666';
    await db.into(db.tasks).insert(TasksCompanion.insert(
          id: id,
          title: 'my local edit',
          priority: 'low',
          isCompleted: false,
          category: 'personal',
          createdAt: DateTime.now(),
        ));
    await db.enqueueOutbox(table: 'tasks', recordId: id, operation: 'upsert', payload: {'title_enc': 'x'});
    final httpClient = MockClient((request) async {
      return http.Response(
        jsonEncode([
          {
            'id': id,
            'table': 'tasks',
            'status': 'conflict',
            'row': {
              'id': id,
              'title_enc': EncryptionService.encryptText('the server version'),
              'description_enc': null,
              'priority': 'high',
              'category': 'work',
              'due_date': null,
              'completed_at': null,
              'google_task_id': null,
              'created_at': '2026-01-01T00:00:00Z',
              'updated_at': '2026-01-02T00:00:00Z',
              'deleted_at': null,
            },
          },
        ]),
        200,
      );
    });

    await containerWith(httpClient).read(syncClientProvider).push();

    final task = await db.select(db.tasks).getSingle();
    expect(task.title, 'the server version');
    expect(task.priority, 'high');
    expect(await db.select(db.outboxEntries).get(), isEmpty);
  });

  test('a pulled health record replaces this device\'s own row for the same day', () async {
    // A health day is stored as its UTC midnight, the server's key for it (F4).
    final day = DateTime.utc(2026, 10, 27);
    await db.into(db.healthRecords).insert(HealthRecordsCompanion.insert(
          id: 'local-today',
          recordDate: day,
          waterIntakeMl: 300,
          sleepMinutes: 0,
          caloriesConsumed: 0,
          exerciseMinutes: 0,
          createdAt: DateTime.now(),
        ));
    final httpClient = MockClient((request) async {
      return http.Response(
        jsonEncode({
          'cursor': 'txid:7',
          'data': {
            'health_records': [
              {
                'id': 'server-today',
                'record_date': day.toUtc().toIso8601String(),
                'weight': null,
                'water_ml': 800,
                'sleep_minutes': 0,
                'calories': 0,
                'exercise_minutes': 0,
                'created_at': '2026-10-27T00:00:00Z',
                'updated_at': '2026-10-27T09:00:00Z',
                'deleted_at': null,
              },
            ],
          },
        }),
        200,
      );
    });

    await containerWith(httpClient).read(syncClientProvider).pull();

    final rows = await db.select(db.healthRecords).get();
    expect(rows.map((r) => (r.id, r.waterIntakeMl)), [('server-today', 800)]);
  });

  test('pull applies message rows onto Drift including tombstones', () async {
    const liveId = 'dddddddd-dddd-dddd-dddd-dddddddddddd';
    const deadId = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee';
    await db.into(db.messages).insert(
          MessagesCompanion.insert(
            id: deadId,
            conversationId: 'old',
            role: 'user',
            content: 'gone',
            createdAt: DateTime.now(),
          ),
        );

    final contentEnc = EncryptionService.encryptText('synced hello');
    final httpClient = MockClient((request) async {
      expect(request.method, 'GET');
      expect(request.url.path, '/api/v3/sync');
      return http.Response(
        jsonEncode({
          'cursor': '2026-01-01T00:00:00Z',
          'data': {
            'messages': [
              {
                'id': liveId,
                'conversation_id': 'conv-9',
                'role': 'user',
                'content_enc': contentEnc,
                'model_used': null,
                'tokens_used': null,
                'citations_enc': null,
                'tool_calls_enc': null,
                'persona': 'orchestrator',
                'created_at': '2026-01-01T00:00:00Z',
                'updated_at': '2026-01-01T00:00:01Z',
                'deleted_at': null,
              },
              {
                'id': deadId,
                'deleted_at': '2026-01-01T00:00:02Z',
              },
            ],
          },
        }),
        200,
      );
    });

    final result = await containerWith(httpClient).read(syncClientProvider).pull();
    expect(result.ok, isTrue);

    final rows = await db.select(db.messages).get();
    expect(rows, hasLength(1));
    expect(rows.single.id, liveId);
    expect(rows.single.content, 'synced hello');
    expect(rows.single.conversationId, 'conv-9');
    expect(rows.single.agentId, 'orchestrator');
  });

  group('two changes to one row within a second (INT-001)', () {
    // The outbox clock is stored in whole seconds, and a server that predates
    // the same-batch rule treats an equal clock as "server is newer". This fake
    // applies exactly that rule, per request, so only the client's own clock
    // can get the second change through.
    late Map<String, ({DateTime clock, bool deleted})> server;
    late List<String> sentClocks;

    MockClient lwwServer() {
      return MockClient((request) async {
        if (request.method == 'GET') return http.Response(jsonEncode(emptyPull()), 200);
        final mutations = (jsonDecode(request.body) as Map<String, dynamic>)['mutations'] as List;
        final results = <Map<String, dynamic>>[];
        for (final m in mutations.cast<Map<String, dynamic>>()) {
          sentClocks.add(m['client_updated_at'] as String);
          final clock = DateTime.parse(m['client_updated_at'] as String);
          final existing = server[m['id']];
          if (existing != null && !existing.clock.isBefore(clock)) {
            results.add({'id': m['id'], 'table': m['table'], 'status': 'conflict'});
            continue;
          }
          server[m['id'] as String] = (clock: clock, deleted: m['op'] == 'delete');
          results.add({'id': m['id'], 'table': m['table'], 'status': 'applied'});
        }
        return http.Response(jsonEncode(results), 200);
      });
    }

    setUp(() {
      server = {};
      sentClocks = [];
      final fixed = DateTime.utc(2026, 11, 17, 9, 0, 0, 300);
      db.outboxClock = () => fixed;
    });

    const id = '77777777-7777-7777-7777-777777777777';

    test('save then forget in one push ends deleted on the server', () async {
      final client = containerWith(lwwServer()).read(syncClientProvider);
      await db.enqueueOutbox(table: 'tasks', recordId: id, operation: 'upsert', payload: {'title_enc': 'x'});
      await db.enqueueOutbox(table: 'tasks', recordId: id, operation: 'delete');

      expect((await client.push()).ok, isTrue);

      expect(server[id]!.deleted, isTrue);
      expect(sentClocks, hasLength(2));
      expect(DateTime.parse(sentClocks[1]).isAfter(DateTime.parse(sentClocks[0])), isTrue);
      expect(await db.select(db.outboxEntries).get(), isEmpty);
    });

    test('save, push, then forget in the same second also ends deleted', () async {
      final client = containerWith(lwwServer()).read(syncClientProvider);
      await db.enqueueOutbox(table: 'tasks', recordId: id, operation: 'upsert', payload: {'title_enc': 'x'});
      await client.push();
      await db.enqueueOutbox(table: 'tasks', recordId: id, operation: 'delete');
      await client.push();

      expect(server[id]!.deleted, isTrue);
      expect(DateTime.parse(sentClocks[1]).isAfter(DateTime.parse(sentClocks[0])), isTrue);
    });

    test('a quick double edit keeps the second', () async {
      final client = containerWith(lwwServer()).read(syncClientProvider);
      await db.enqueueOutbox(table: 'tasks', recordId: id, operation: 'upsert', payload: {'title_enc': 'a'});
      await client.push();
      await db.enqueueOutbox(table: 'tasks', recordId: id, operation: 'upsert', payload: {'title_enc': 'b'});
      await client.push();
      expect(sentClocks, hasLength(2));
      expect(DateTime.parse(sentClocks[1]).isAfter(DateTime.parse(sentClocks[0])), isTrue);
    });

    test('other rows keep their own clock', () async {
      final client = containerWith(lwwServer()).read(syncClientProvider);
      await db.enqueueOutbox(table: 'tasks', recordId: id, operation: 'upsert', payload: {'title_enc': 'a'});
      await db.enqueueOutbox(
        table: 'tasks', recordId: '88888888-8888-8888-8888-888888888888', operation: 'upsert', payload: {'title_enc': 'b'},
      );
      await client.push();
      expect(sentClocks[0], sentClocks[1]);
    });
  });

  test('an edit refused because the row was deleted elsewhere deletes it here (INT-005)', () async {
    const id = '99999999-9999-9999-9999-999999999999';
    await db.into(db.tasks).insert(TasksCompanion.insert(
          id: id,
          title: 'edited offline',
          priority: 'low',
          isCompleted: false,
          category: 'personal',
          createdAt: DateTime.now(),
        ));
    await db.enqueueOutbox(table: 'tasks', recordId: id, operation: 'upsert', payload: {'title_enc': 'x'});
    final httpClient = MockClient((request) async {
      return http.Response(
        jsonEncode([
          {
            'id': id,
            'table': 'tasks',
            'status': 'conflict',
            'detail': 'deleted on the server',
            'row': {'id': id, 'title_enc': '', 'deleted_at': '2026-01-02T00:00:00Z'},
          },
        ]),
        200,
      );
    });

    await containerWith(httpClient).read(syncClientProvider).push();

    expect(await db.select(db.tasks).get(), isEmpty);
    expect(await db.select(db.outboxEntries).get(), isEmpty);
  });

  group('a paged pull (INT-013)', () {
    Map<String, dynamic> taskRow(String id, String title) => {
          'id': id,
          'title_enc': EncryptionService.encryptText(title),
          'description_enc': null,
          'priority': 'medium',
          'category': 'personal',
          'due_date': null,
          'completed_at': null,
          'google_task_id': null,
          'created_at': '2026-01-01T00:00:00Z',
          'updated_at': '2026-01-01T00:00:00Z',
          'deleted_at': null,
        };

    test('follows next_page and stores only the last page\'s cursor', () async {
      await db.setSyncCursor('txid:5');
      final queries = <Map<String, String>>[];
      final httpClient = MockClient((request) async {
        if (request.url.path != '/api/v3/sync') return http.Response('', 404);
        queries.add(request.url.queryParameters);
        final page = request.url.queryParameters['page'];
        if (page == null) {
          return http.Response(
            jsonEncode({
              'cursor': 'txid:5',
              'has_more': true,
              'next_page': 'page1.5.40.6.-.-',
              'data': {'tasks': [taskRow('aaaaaaaa-0000-0000-0000-000000000001', 'one')]},
            }),
            200,
          );
        }
        return http.Response(
          jsonEncode({
            'cursor': 'txid:40',
            'has_more': false,
            'data': {'tasks': [taskRow('aaaaaaaa-0000-0000-0000-000000000002', 'two')]},
          }),
          200,
        );
      });

      final result = await containerWith(httpClient).read(syncClientProvider).pull();

      expect(result.ok, isTrue);
      expect(queries, hasLength(2));
      expect(queries[0]['since'], 'txid:5');
      expect(queries[0]['limit'], '${SyncClient.pullPageSize}');
      expect(queries[0].containsKey('page'), isFalse);
      expect(queries[1]['page'], 'page1.5.40.6.-.-');
      expect(queries[1]['limit'], '${SyncClient.pullPageSize}');
      expect((await db.select(db.tasks).get()).map((t) => t.title).toSet(), {'one', 'two'});
      expect(await db.getSyncCursor(), 'txid:40');
    });

    test('a page that fails leaves the stored cursor where it was', () async {
      await db.setSyncCursor('txid:5');
      final httpClient = MockClient((request) async {
        if (request.url.queryParameters['page'] == null) {
          return http.Response(
            jsonEncode({'cursor': 'txid:5', 'has_more': true, 'next_page': 'p2', 'data': <String, dynamic>{}}),
            200,
          );
        }
        return http.Response('boom', 500);
      });

      final result = await containerWith(httpClient).read(syncClientProvider).pull();

      expect(result.ok, isFalse);
      expect(await db.getSyncCursor(), 'txid:5');
    });

    test('a server that does not page answers in one response', () async {
      final httpClient = MockClient((request) async {
        return http.Response(jsonEncode({'cursor': 'txid:9', 'data': <String, dynamic>{}}), 200);
      });
      expect((await containerWith(httpClient).read(syncClientProvider).pull()).ok, isTrue);
      expect(await db.getSyncCursor(), 'txid:9');
    });
  });

  group('no access token (INT-028)', () {
    ProviderContainer containerWithoutToken({required bool hasRefreshToken}) {
      final container = ProviderContainer(
        overrides: [
          tokenManagerProvider.overrideWith((ref) => _NoTokenManager(ref, hasRefreshToken: hasRefreshToken)),
          syncClientProvider.overrideWith(
            (ref) => SyncClient(db, ref, httpClient: MockClient((_) async => http.Response('', 500))),
          ),
        ],
      );
      addTearDown(container.dispose);
      return container;
    }

    test('with the session still on disk, it is offline rather than signed out', () async {
      await db.enqueueOutbox(table: 'tasks', recordId: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', operation: 'delete');
      final client = containerWithoutToken(hasRefreshToken: true).read(syncClientProvider);
      expect((await client.push()).failure, SyncFailureKind.network);
      expect((await client.pull()).failure, SyncFailureKind.network);
      expect((await client.synchronize()).failure, SyncFailureKind.network);
    });

    test('with no session left, it is unauthorized', () async {
      await db.enqueueOutbox(table: 'tasks', recordId: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', operation: 'delete');
      final client = containerWithoutToken(hasRefreshToken: false).read(syncClientProvider);
      expect((await client.push()).failure, SyncFailureKind.unauthorized);
      expect((await client.pull()).failure, SyncFailureKind.unauthorized);
    });
  });
}

/// A token manager that cannot produce an access token right now: offline
/// with an expired one ([hasRefreshToken]), or signed out.
class _NoTokenManager extends TokenManager {
  _NoTokenManager(super.ref, {required this.hasRefreshToken});

  final bool hasRefreshToken;

  @override
  Future<String?> getValidAccessToken() async => null;

  @override
  Future<bool> hasCachedRefreshToken() async => hasRefreshToken;
}
