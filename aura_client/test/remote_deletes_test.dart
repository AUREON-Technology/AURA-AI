// Deletes that reach the server (audit F-07): memory "forget" and chat
// delete. Before, the client deleted a memory by a local hash the server had
// never issued (404, only debugPrint-ed), so the memory kept being recalled;
// and deleting a chat left the server's transcript of it in place.

import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/core/sync/remote_deletes.dart';
import 'package:aura_client/core/sync/sync_client.dart';
import 'package:aura_client/features/chat/data/chat_repository.dart';
import 'package:aura_client/features/chat/data/memory_repository.dart';
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

const _serverId = '9f1c2d3e-0000-4000-8000-000000000001';

/// A fake `/api/v3/memory` + `/api/v3/sessions` server.
class _FakeServer {
  final memories = <String, String>{};
  final requests = <String>[];
  bool deletesFail = false;

  Future<http.Response> handle(http.Request request) async {
    requests.add('${request.method} ${request.url.path}');
    final path = request.url.path;
    if (request.method == 'POST' && path == '/api/v3/memory') {
      final content = (jsonDecode(request.body) as Map<String, dynamic>)['content'] as String;
      memories[_serverId] = content;
      return http.Response(jsonEncode({'id': _serverId, 'content': content}), 200);
    }
    if (request.method == 'GET' && path == '/api/v3/memory') {
      return http.Response(
        jsonEncode({
          'enabled': true,
          'memories': [
            for (final e in memories.entries) {'id': e.key, 'content': e.value, 'kind': 'semantic'},
          ],
        }),
        200,
      );
    }
    if (request.method == 'DELETE' && path.startsWith('/api/v3/memory/')) {
      if (deletesFail) return http.Response('unavailable', 500);
      final removed = memories.remove(path.substring('/api/v3/memory/'.length));
      return removed == null ? http.Response('{}', 404) : http.Response(jsonEncode({'deleted': true}), 200);
    }
    if (request.method == 'DELETE' && path.startsWith('/api/v3/sessions/')) {
      if (deletesFail) return http.Response('unavailable', 500);
      return http.Response(jsonEncode({'deleted': true}), 200);
    }
    return http.Response('not found', 404);
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late _FakeServer server;
  late ProviderContainer container;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    server = _FakeServer();
    final httpClient = MockClient(server.handle);
    container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWith((ref) {
          ref.onDispose(db.close);
          return db;
        }),
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        memoryRepositoryProvider.overrideWith((ref) => MemoryRepository(db, ref, httpClient: httpClient)),
        remoteDeletesProvider.overrideWith((ref) => RemoteDeletes(db, ref, httpClient: httpClient)),
        syncClientProvider.overrideWith((ref) => SyncClient(db, ref, httpClient: httpClient)),
      ],
    );
  });

  tearDown(() => container.dispose());

  MemoryRepository memories() => container.read(memoryRepositoryProvider);

  test('a saved memory takes the server id, and forget deletes by that id', () async {
    await memories().saveMemory('I live in Cairo');

    final local = await db.select(db.userMemories).getSingle();
    expect(local.id, _serverId);
    expect(local.isSynced, isTrue);
    final upsert = await db.select(db.outboxEntries).getSingle();
    expect(upsert.recordId, _serverId, reason: 'the sync copy must share the id a delete will name');

    await memories().deleteMemory(_serverId);

    expect(server.requests.last, 'DELETE /api/v3/memory/$_serverId');
    expect(server.memories, isEmpty);
    expect(await db.select(db.userMemories).get(), isEmpty);
    expect(await db.select(db.pendingDeletes).get(), isEmpty);
    final tombstone = (await db.select(db.outboxEntries).get()).singleWhere((e) => e.operation == 'delete');
    expect(tombstone.recordId, _serverId);
  });

  test('a forget that fails is retried, and the memory does not come back meanwhile', () async {
    await memories().saveMemory('I live in Cairo');
    server.deletesFail = true;

    await memories().deleteMemory(_serverId);
    expect(server.memories, contains(_serverId), reason: 'the server refused the delete');

    // The server still lists it; the client must not resurrect it.
    final listed = await memories().syncMemories();
    expect(listed.map((m) => m.id), isNot(contains(_serverId)));
    expect(await db.select(db.userMemories).get(), isEmpty);
    expect(await RemoteDeletes(db, container.read(_refProbe)).pendingIds(RemoteDeleteKind.memory), {_serverId});

    // Once the server answers, the retry lands and nothing is pending.
    server.deletesFail = false;
    final after = await memories().syncMemories();
    expect(after, isEmpty);
    expect(server.memories, isEmpty);
    expect(await db.select(db.pendingDeletes).get(), isEmpty);
  });

  test('a sync retries pending forgets too', () async {
    await memories().saveMemory('I live in Cairo');
    server.deletesFail = true;
    await memories().deleteMemory(_serverId);
    server.deletesFail = false;
    await db.delete(db.outboxEntries).go(); // isolate the remote-delete path

    final syncServer = MockClient((request) async {
      if (request.method == 'GET' && request.url.path == '/api/v3/sync') {
        return http.Response(jsonEncode({'cursor': 'txid:1', 'data': <String, dynamic>{}}), 200);
      }
      return server.handle(request);
    });
    await SyncClient(db, container.read(_refProbe), httpClient: syncServer).synchronize();

    expect(server.memories, isEmpty);
    expect(await db.select(db.pendingDeletes).get(), isEmpty);
  });

  test('memories the server no longer has are dropped from the local cache', () async {
    await memories().saveMemory('I live in Cairo');
    server.memories.clear(); // forgotten on another device

    await memories().syncMemories();

    expect(await db.select(db.userMemories).get(), isEmpty);
  });

  test('a memory tombstone pulled from sync removes the local copy', () async {
    await memories().saveMemory('I live in Cairo');
    final pull = MockClient((request) async => http.Response(
          jsonEncode({
            'cursor': 'txid:9',
            'data': {
              'memories': [
                {'id': _serverId, 'fact_enc': '', 'deleted_at': '2026-10-28T10:00:00Z'},
              ],
            },
          }),
          200,
        ));

    await SyncClient(db, container.read(_refProbe), httpClient: pull).pull();

    expect(await db.select(db.userMemories).get(), isEmpty);
  });

  test('deleting a chat clears the server transcript and tombstones its messages', () async {
    await db.into(db.messages).insert(MessagesCompanion.insert(
          id: '11111111-1111-1111-1111-111111111111',
          conversationId: 'session_42',
          role: 'user',
          content: 'my secret',
          createdAt: DateTime.now(),
        ));

    await container.read(chatRepositoryProvider).clearConversation('session_42');

    expect(server.requests, contains('DELETE /api/v3/sessions/session_42'));
    expect(await db.select(db.messages).get(), isEmpty);
    final tombstone = await db.select(db.outboxEntries).getSingle();
    expect((tombstone.targetTable, tombstone.operation), ('messages', 'delete'));
    expect(await db.select(db.pendingDeletes).get(), isEmpty);
  });

  test('a chat delete the server did not confirm stays pending for the next sync', () async {
    server.deletesFail = true;
    await container.read(chatRepositoryProvider).clearConversation('session_42');

    final pending = await db.select(db.pendingDeletes).getSingle();
    expect((pending.targetTable, pending.recordId), ('chat_session', 'session_42'));
  });
}

/// A bare [Ref] for constructing clients by hand inside a test.
final _refProbe = Provider<Ref>((ref) => ref);
