import 'dart:convert';

import 'package:aura_client/core/ai/knowledge/knowledge_center.dart';
import 'package:aura_client/core/auth/token_manager.dart';
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
  test('ingestDocument POSTs JWT to /api/v3/knowledge/documents', () async {
    http.Request? recorded;
    final httpClient = MockClient((request) async {
      recorded = request;
      return http.Response('{"document_id":"doc-1","chunks":1,"created":true}', 200);
    });

    final container = ProviderContainer(
      overrides: [
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        knowledgeCenterProvider.overrideWith(
          (ref) => KnowledgeCenter(ref, httpClient: httpClient),
        ),
      ],
    );
    addTearDown(container.dispose);

    final ok = await container.read(knowledgeCenterProvider).ingestDocument(
          docId: 'doc-1',
          content: 'hello world',
          metadata: {'source': 'test'},
        );

    expect(ok, isTrue);
    expect(recorded, isNotNull);
    expect(recorded!.method, 'POST');
    expect(recorded!.url.path, '/api/v3/knowledge/documents');
    expect(recorded!.headers['Authorization'], 'Bearer jwt-test');
    final body = jsonDecode(recorded!.body) as Map<String, dynamic>;
    expect(body['document_id'], 'doc-1');
    expect(body['title'], 'doc-1');
    expect(body['content'], 'hello world');
    expect(recorded!.url.path.contains('/api/rag'), isFalse);
  });

  test('queryKnowledge POSTs JWT to /api/v3/knowledge/search', () async {
    http.Request? recorded;
    final httpClient = MockClient((request) async {
      recorded = request;
      return http.Response(
        jsonEncode({
          'query': 'alpha',
          'results': [
            {'content': 'hit', 'score': 0.91, 'document_id': 'doc-1'},
          ],
        }),
        200,
      );
    });

    final container = ProviderContainer(
      overrides: [
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        knowledgeCenterProvider.overrideWith(
          (ref) => KnowledgeCenter(ref, httpClient: httpClient),
        ),
      ],
    );
    addTearDown(container.dispose);

    final results = await container.read(knowledgeCenterProvider).queryKnowledge('alpha');

    expect(results, hasLength(1));
    expect(results.first['document_id'], 'doc-1');
    expect(recorded!.url.path, '/api/v3/knowledge/search');
    expect(recorded!.headers['Authorization'], 'Bearer jwt-test');
    final body = jsonDecode(recorded!.body) as Map<String, dynamic>;
    expect(body['query'], 'alpha');
    expect(body['limit'], 3);
  });
}
