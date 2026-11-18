// C1 / F22 / INT-109: a memory saved while the server cannot be reached is kept in a durable
// queue and sent once it can be, under one stable idempotency key; a refusal is not retried
// forever but reported.
//
// A failed POST used to be only logged: the memory stayed in this device's cache, the assistant
// never learned it, and `syncMemories` retried deletes but never saves.

import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/core/sync/sync_client.dart';
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

/// The memory API: down, up, or refusing every save, switchable mid-test.
class _Server {
  String mode = 'down';
  final posts = <http.Request>[];
  final stored = <Map<String, dynamic>>[];

  http.Client client() => MockClient((request) async {
        if (request.method == 'POST') {
          posts.add(request);
          if (mode == 'down') return http.Response('{"detail":"unavailable"}', 503);
          if (mode == 'refuse') return http.Response('{"detail":"content too long"}', 422);
          final body = jsonDecode(request.body) as Map<String, dynamic>;
          final id = 'mem-${stored.length + 1}';
          stored.add({'id': id, 'content': body['content'], 'kind': body['kind']});
          return http.Response(jsonEncode({'id': id, 'content': body['content']}), 200);
        }
        if (request.method == 'GET') {
          if (mode == 'down') throw http.ClientException('offline');
          return http.Response(jsonEncode({'enabled': true, 'memories': stored}), 200);
        }
        return http.Response('{}', 200);
      });
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;
  late _Server server;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
    server = _Server();
  });

  tearDown(() async => db.close());

  ProviderContainer scope() {
    final client = server.client();
    final container = ProviderContainer(overrides: [
      databaseProvider.overrideWith((ref) => db),
      tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
      memoryRepositoryProvider.overrideWith((ref) => MemoryRepository(db, ref, httpClient: client)),
    ]);
    addTearDown(container.dispose);
    return container;
  }

  test('an offline save is queued, then sent once with the same key when the server is back',
      () async {
    final c = scope();
    final repo = c.read(memoryRepositoryProvider);

    expect(await repo.saveMemory('I live in Cairo', kind: 'profile', subject: 'home'),
        MemorySaveOutcome.queued);
    final local = await db.select(db.userMemories).getSingle();
    expect(local.isSynced, isFalse);
    expect(await repo.pendingSaveIds(), {local.id});

    server.mode = 'up';
    final refused = await repo.flushPendingSaves();

    expect(refused, isEmpty);
    expect(server.posts, hasLength(2), reason: 'the first attempt, then one retry');
    final keys = server.posts.map((r) => r.headers['Idempotency-Key']).toSet();
    expect(keys, hasLength(1), reason: 'one logical save, one key: the server replays, never doubles');
    expect(keys.single, contains(local.id));
    final retried = jsonDecode(server.posts.last.body) as Map<String, dynamic>;
    expect(retried, {'content': 'I live in Cairo', 'kind': 'profile', 'subject': 'home'});

    final saved = await db.select(db.userMemories).getSingle();
    expect(saved.id, 'mem-1', reason: 're-keyed to the server id, so a later forget reaches it');
    expect(saved.isSynced, isTrue);
    expect(await repo.pendingSaveIds(), isEmpty);

    await repo.flushPendingSaves();
    expect(server.posts, hasLength(2), reason: 'nothing left to send');
  });

  test('the regular sync sends a queued save, not only the memory screen', () async {
    final client = server.client();
    final c = ProviderContainer(overrides: [
      databaseProvider.overrideWith((ref) => db),
      tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
      memoryRepositoryProvider.overrideWith((ref) => MemoryRepository(db, ref, httpClient: client)),
      syncClientProvider.overrideWith((ref) => SyncClient(db, ref, httpClient: client)),
    ]);
    addTearDown(c.dispose);
    expect(await c.read(memoryRepositoryProvider).saveMemory('I live in Cairo'),
        MemorySaveOutcome.queued);
    server.mode = 'up';

    await c.read(syncClientProvider).synchronize();

    final memoryPosts = server.posts.where((r) => r.url.path == '/api/v3/memory').toList();
    expect(memoryPosts, hasLength(2), reason: 'the queued attempt, then the retry during sync');
    expect(await c.read(memoryRepositoryProvider).pendingSaveIds(), isEmpty);
  });

  test('syncMemories sends queued saves first, so the list it fetches already has them', () async {
    final c = scope();
    final repo = c.read(memoryRepositoryProvider);
    await repo.saveMemory('I live in Cairo');

    server.mode = 'up';
    final items = await repo.syncMemories();

    expect(items.map((i) => i.content), ['I live in Cairo']);
    expect(server.posts, hasLength(2));
    expect(await repo.pendingSaveIds(), isEmpty);
    expect((await db.select(db.userMemories).get()).map((m) => m.id), ['mem-1']);
  });

  test('a queued save that the server refuses is reported once, not retried forever', () async {
    final c = scope();
    final repo = c.read(memoryRepositoryProvider);
    await repo.saveMemory('I live in Cairo');

    server.mode = 'refuse';
    final refused = await repo.flushPendingSaves();

    expect(refused, ['I live in Cairo']);
    expect(c.read(memorySaveRefusalsProvider), ['I live in Cairo']);
    expect(await repo.pendingSaveIds(), isEmpty);
    expect(await db.select(db.userMemories).get(), isEmpty,
        reason: 'not shown as a memory the assistant has');

    await repo.flushPendingSaves();
    expect(server.posts, hasLength(2), reason: 'a refusal is final');
  });

  test('a save refused at once says so and leaves nothing queued', () async {
    server.mode = 'refuse';
    final c = scope();
    final repo = c.read(memoryRepositoryProvider);

    expect(await repo.saveMemory('I live in Cairo'), MemorySaveOutcome.refused);

    expect(await repo.pendingSaveIds(), isEmpty);
    expect(await db.select(db.userMemories).get(), isEmpty);
    expect(await db.select(db.outboxEntries).get(), isEmpty);
  });

  test('a save the server takes at once is not queued', () async {
    server.mode = 'up';
    final c = scope();
    final repo = c.read(memoryRepositoryProvider);

    expect(await repo.saveMemory('I live in Cairo'), MemorySaveOutcome.saved);

    expect(await repo.pendingSaveIds(), isEmpty);
    expect(server.posts, hasLength(1));
  });
}
