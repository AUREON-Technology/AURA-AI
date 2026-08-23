import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/chat/data/feedback_repository.dart';
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

  test('submit POSTs /api/v3/feedback and caches the correction locally', () async {
    await EncryptionService.init();
    final db = AppDatabase.forTesting(NativeDatabase.memory());
    http.Request? recorded;
    final httpClient = MockClient((request) async {
      recorded = request;
      return http.Response('{"recorded":true}', 200);
    });

    final container = ProviderContainer(
      overrides: [
        databaseProvider.overrideWith((ref) {
          ref.onDispose(db.close);
          return db;
        }),
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        auraApiClientProvider.overrideWith(
          (ref) => AuraApiClient(ref, httpClient: httpClient),
        ),
      ],
    );
    addTearDown(container.dispose);

    await container.read(feedbackRepositoryProvider).submit(
          turnId: 'turn-1',
          rating: 4,
          correction: 'Say Cairo, not Egypt only.',
          response: 'You live in Egypt.',
        );

    expect(recorded!.method, 'POST');
    expect(recorded!.url.path, '/api/v3/feedback');
    final body = jsonDecode(recorded!.body) as Map<String, dynamic>;
    expect(body['turn_id'], 'turn-1');
    expect(body['rating'], 4);
    expect(body['correction'], 'Say Cairo, not Egypt only.');

    final logs = await db.select(db.learningLogs).get();
    expect(logs, hasLength(1));
    expect(logs.first.rating, 4);
    expect(logs.first.correction, 'Say Cairo, not Egypt only.');
  });
}
