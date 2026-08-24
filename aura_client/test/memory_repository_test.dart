import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/security/encryption_service.dart';
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

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late AppDatabase db;

  setUp(() async {
    await EncryptionService.init();
    db = AppDatabase.forTesting(NativeDatabase.memory());
  });

  test('syncMemories GETs JWT to /api/v3/memory and maps the memories envelope', () async {
    http.Request? recorded;
    final httpClient = MockClient((request) async {
      recorded = request;
      return http.Response(
        jsonEncode({
          'enabled': true,
          'memories': [
            {'id': 'mem-1', 'content': 'User likes tea', 'kind': 'semantic'},
          ],
        }),
        200,
      );
    });

    final container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWith((ref) {
          ref.onDispose(db.close);
          return db;
        }),
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        memoryRepositoryProvider.overrideWith(
          (ref) => MemoryRepository(db, ref, httpClient: httpClient),
        ),
      ],
    );
    addTearDown(container.dispose);

    final items = await container.read(memoryRepositoryProvider).syncMemories();

    expect(items, hasLength(1));
    expect(items.first.id, 'mem-1');
    expect(items.first.content, 'User likes tea');
    expect(items.first.kind, 'semantic');
    expect(recorded!.method, 'GET');
    expect(recorded!.url.path, '/api/v3/memory');
    expect(recorded!.headers['Authorization'], 'Bearer jwt-test');
    expect(recorded!.url.path.contains('/api/memory'), isFalse);
    // Regression: this call replaces the *entire* local cache below, so it must ask for the
    // server's max rather than falling through to GET /memory's own default of 20 -- which
    // silently dropped every memory past the 20 most recent on every sync for any user with
    // more than 20 (Phase B2).
    expect(recorded!.url.queryParameters['limit'], '100');
  });

  test('saveMemory POSTs JWT content to /api/v3/memory', () async {
    http.Request? recorded;
    final httpClient = MockClient((request) async {
      recorded = request;
      return http.Response(jsonEncode({'id': 'new', 'content': 'I live in Cairo'}), 200);
    });

    final container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWith((ref) {
          ref.onDispose(db.close);
          return db;
        }),
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        memoryRepositoryProvider.overrideWith(
          (ref) => MemoryRepository(db, ref, httpClient: httpClient),
        ),
      ],
    );
    addTearDown(container.dispose);

    await container.read(memoryRepositoryProvider).saveMemory('I live in Cairo');

    expect(recorded!.method, 'POST');
    expect(recorded!.url.path, '/api/v3/memory');
    expect(recorded!.headers['Authorization'], 'Bearer jwt-test');
    final body = jsonDecode(recorded!.body) as Map<String, dynamic>;
    expect(body['content'], 'I live in Cairo');
    expect(body.containsKey('fact'), isFalse);
  });

  test('deleteMemory DELETEs JWT to /api/v3/memory/{id}', () async {
    http.Request? recorded;
    final httpClient = MockClient((request) async {
      recorded = request;
      return http.Response(jsonEncode({'deleted': true}), 200);
    });

    final container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWith((ref) {
          ref.onDispose(db.close);
          return db;
        }),
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        memoryRepositoryProvider.overrideWith(
          (ref) => MemoryRepository(db, ref, httpClient: httpClient),
        ),
      ],
    );
    addTearDown(container.dispose);

    await container.read(memoryRepositoryProvider).deleteMemory('mem-9');

    expect(recorded!.method, 'DELETE');
    expect(recorded!.url.path, '/api/v3/memory/mem-9');
    expect(recorded!.headers['Authorization'], 'Bearer jwt-test');
  });
}
