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
}
