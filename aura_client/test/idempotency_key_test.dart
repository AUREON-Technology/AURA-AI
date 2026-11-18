// Every client write carries an `Idempotency-Key` (audit FSA-046 / E2-03).
//
// The server has deduplicated keyed writes since roadmap P1.4; no client sent the header, so a
// retried write created a second record or got a 404. These pin the header on each write path
// and its absence on reads, through the real classes against a recording MockClient.

import 'dart:convert';

import 'package:aura_client/core/ai/engineer/engineer_workspaces.dart';
import 'package:aura_client/core/ai/knowledge/knowledge_center.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/core/sync/remote_deletes.dart';
import 'package:aura_client/features/chat/data/feedback_repository.dart';
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

final _uuidV4 = RegExp(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$');

class _Recorder {
  final List<http.Request> requests = [];
  int Function(http.Request request)? status;
  String Function(http.Request request)? body;

  http.Client client() => MockClient((request) async {
        requests.add(request);
        return http.Response(body?.call(request) ?? '{}', status?.call(request) ?? 200);
      });

  Iterable<String?> keysFor(String method) =>
      requests.where((r) => r.method == method).map((r) => r.headers['Idempotency-Key']);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late AppDatabase db;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
  });

  ProviderContainer scope(http.Client httpClient) {
    final container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWith((ref) {
          ref.onDispose(db.close);
          return db;
        }),
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        auraApiClientProvider.overrideWith((ref) => AuraApiClient(ref, httpClient: httpClient)),
        knowledgeCenterProvider.overrideWith((ref) => KnowledgeCenter(ref, httpClient: httpClient)),
        memoryRepositoryProvider.overrideWith((ref) => MemoryRepository(db, ref, httpClient: httpClient)),
        remoteDeletesProvider.overrideWith((ref) => RemoteDeletes(db, ref, httpClient: httpClient)),
        engineerWorkspacesClientProvider.overrideWith((ref) => EngineerWorkspacesClient(ref, httpClient: httpClient)),
      ],
    );
    addTearDown(container.dispose);
    return container;
  }

  group('KnowledgeCenter', () {
    test('a document POST carries a fresh key and a search GET carries none', () async {
      final rec = _Recorder()
        ..body = (r) => r.method == 'POST' && r.url.path.endsWith('/documents')
            ? '{"document_id":"doc-1","chunks":1,"created":true}'
            : '{"results":[]}';
      final center = scope(rec.client()).read(knowledgeCenterProvider);
      await center.ingestDocument(docId: 'doc-1', content: 'hello', metadata: {});
      await center.ingestDocument(docId: 'doc-2', content: 'world', metadata: {});
      final keys = rec.keysFor('POST').toList();
      expect(keys, hasLength(2));
      expect(keys.every((k) => k != null && _uuidV4.hasMatch(k)), isTrue, reason: '$keys');
      expect(keys.toSet(), hasLength(2), reason: 'each logical write gets its own key');
    });

    test('a document DELETE carries a key', () async {
      final rec = _Recorder()..body = (_) => '{"deleted":true}';
      final center = scope(rec.client()).read(knowledgeCenterProvider);
      await center.deleteDocument('doc-1');
      expect(rec.keysFor('DELETE').single, matches(_uuidV4));
    });

    test('the 401 re-send of one write reuses the same key', () async {
      var calls = 0;
      final rec = _Recorder()
        ..status = ((_) => ++calls == 1 ? 401 : 200)
        ..body = (_) => '{"document_id":"doc-1","chunks":1,"created":true}';
      final center = scope(rec.client()).read(knowledgeCenterProvider);
      await center.ingestDocument(docId: 'doc-1', content: 'hello', metadata: {});
      final keys = rec.keysFor('POST').toList();
      expect(keys, hasLength(2));
      expect(keys.first, keys.last, reason: 'a re-sent request is the same write to the server');
    });
  });

  group('MemoryRepository', () {
    test('saving a memory POSTs with a key; listing GETs without one', () async {
      final rec = _Recorder()
        ..body = (r) => r.method == 'POST'
            ? jsonEncode({'id': 'mem-1', 'content': 'I live in Cairo', 'kind': 'semantic'})
            : jsonEncode({'enabled': true, 'memories': []});
      final memories = scope(rec.client()).read(memoryRepositoryProvider);
      await memories.saveMemory('I live in Cairo');
      await memories.syncMemories();
      // Stable per saved memory (F22): a queued save retried on a later sync presents the same
      // key, so the server replays its first answer instead of storing the memory twice.
      final key = rec.keysFor('POST').single!;
      expect(key, startsWith('save-memory-'));
      expect(key.substring('save-memory-'.length), matches(_uuidV4));
      expect(rec.keysFor('GET').every((k) => k == null), isTrue);
    });
  });

  group('RemoteDeletes', () {
    test('a queued delete keeps one stable key across retries, so the server replays rather than 404s',
        () async {
      var attempts = 0;
      final rec = _Recorder()
        ..status = ((r) => r.method == 'DELETE' && ++attempts == 1 ? 500 : 200)
        ..body = (_) => '{"deleted":true}';
      final container = scope(rec.client());
      final deletes = container.read(remoteDeletesProvider);
      expect(await deletes.request(RemoteDeleteKind.memory, 'mem-9'), isFalse);
      await deletes.flush();
      final keys = rec.keysFor('DELETE').toList();
      expect(keys, hasLength(2));
      expect(keys.first, isNotNull);
      expect(keys.first, keys.last, reason: 'the same pending delete must present the same key');
      expect(keys.first, contains('mem-9'));
    });
  });

  group('AuraApiClient (feedback)', () {
    test('feedback POSTs with a key', () async {
      final rec = _Recorder()..body = (_) => '{"recorded":true}';
      await scope(rec.client()).read(feedbackRepositoryProvider).submit(turnId: 't1', rating: 5);
      expect(rec.keysFor('POST').single, matches(_uuidV4));
    });

    test('a GET through the shared client carries none', () async {
      final rec = _Recorder();
      await scope(rec.client()).read(auraApiClientProvider).get('/api/v3/health');
      expect(rec.keysFor('GET').single, isNull);
    });
  });

  group('EngineerWorkspaces', () {
    test('destroying a workspace DELETEs with a key; listing GETs without one', () async {
      final rec = _Recorder()
        ..body = (r) => r.method == 'GET' ? '{"enabled":true,"workspaces":[],"ttl_hours":72}' : '{}';
      final client = scope(rec.client()).read(engineerWorkspacesClientProvider);
      await client.list();
      expect(await client.destroy('ws-1'), isTrue);
      expect(rec.keysFor('DELETE').single, matches(_uuidV4));
      expect(rec.keysFor('GET').single, isNull);
    });
  });
}
