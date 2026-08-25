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

  test('searchKnowledge reports searched_but_empty', () async {
    final httpClient = MockClient((request) async {
      return http.Response(
        jsonEncode({
          'query': 'zzz',
          'results': [],
          'searched_but_empty': true,
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

    final result = await container.read(knowledgeCenterProvider).searchKnowledge('zzz');
    expect(result.results, isEmpty);
    expect(result.searchedButEmpty, isTrue);
  });

  test('uploadPdf() POSTs multipart form data to /api/v3/knowledge/documents/upload', () async {
    http.Request? recorded;
    final httpClient = MockClient((request) async {
      recorded = request;
      return http.Response(
        jsonEncode({'document_id': 'doc-2', 'chunks': 3, 'created': true, 'quarantined_chunks': 0}),
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

    final result = await container.read(knowledgeCenterProvider).uploadPdf(
          title: 'Handbook',
          bytes: [37, 80, 68, 70],
          filename: 'handbook.pdf',
        );

    expect(result.ok, isTrue);
    expect(result.documentId, 'doc-2');
    expect(recorded, isNotNull);
    expect(recorded!.method, 'POST');
    expect(recorded!.url.path, '/api/v3/knowledge/documents/upload');
    expect(recorded!.headers['Authorization'], 'Bearer jwt-test');
    expect(recorded!.bodyBytes, isNotEmpty);
  });

  test('uploadPdf() reports failure on a non-2xx response', () async {
    final httpClient = MockClient((request) async {
      return http.Response(jsonEncode({'error': 'validation_error'}), 400);
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

    final result = await container.read(knowledgeCenterProvider).uploadPdf(
          title: 'Bad file',
          bytes: [1, 2, 3],
        );

    expect(result.ok, isFalse);
  });

  test('listDocuments maps the library envelope', () async {
    final httpClient = MockClient((request) async {
      expect(request.method, 'GET');
      return http.Response(
        jsonEncode({
          'documents': [
            {
              'document_id': 'doc-9',
              'title': 'Notes',
              'version': 2,
              'status': 'quarantined',
              'created_at': '2026-01-01T00:00:00Z',
            },
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

    final docs = await container.read(knowledgeCenterProvider).listDocuments();
    expect(docs, hasLength(1));
    expect(docs.first.id, 'doc-9');
    expect(docs.first.isQuarantined, isTrue);
  });
}
