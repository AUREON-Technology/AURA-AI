// The knowledge client against the gateway's real contract (aura_core/api/v3.py).
//
// INT-016: `GET /knowledge/documents` returns 50 by default (max 100) with limit/offset; the
//          client sent no parameters, so documents past the 50th could not be seen or deleted.
// INT-017: `POST /knowledge/documents` embeds synchronously, but the client gave it 10 s and a
//          new Idempotency-Key on every retry (a saved document reported as failed, then saved
//          twice). The multipart upload had no timeout at all.
// INT-021: 422 limit_bytes, 429, 503 and skipped_reason all became one message, "Saved." showed
//          for skipped documents, nothing checked the 8 MB limit first, and remove() threw.

import 'dart:async';
import 'dart:convert';

import 'package:aura_client/core/ai/knowledge/knowledge_center.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/network/idempotency.dart';
import 'package:aura_client/features/knowledge/domain/knowledge_document.dart';
import 'package:aura_client/features/knowledge/presentation/knowledge_view_model.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _Tokens extends TokenManager {
  _Tokens(super.ref);

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

/// Serves `GET /knowledge/documents` the way the gateway does: default 50, max 100.
http.Response _libraryPage(http.Request request, int total) {
  final rawLimit = request.url.queryParameters['limit'];
  final limit = rawLimit == null ? 50 : int.parse(rawLimit);
  if (limit < 1 || limit > 100) {
    return http.Response(jsonEncode({'detail': [{'type': 'less_than_equal'}]}), 422);
  }
  final offset = int.parse(request.url.queryParameters['offset'] ?? '0');
  final docs = [
    for (var i = offset; i < total && i < offset + limit; i++)
      {
        'document_id': 'doc-$i',
        'title': 'Doc $i',
        'version': 1,
        'status': 'active',
        'cag_pinned': false,
        'created_at': '2026-11-17T00:00:00+00:00',
      },
  ];
  return http.Response(jsonEncode({'documents': docs}), 200);
}

http.Response _cag() => http.Response(jsonEncode({'enabled': false, 'tokens': 0, 'budget': 0}), 200);

ProviderContainer _container(
  MockClient client, {
  Duration ingestTimeout = KnowledgeCenter.defaultIngestTimeout,
  Duration uploadTimeout = KnowledgeCenter.defaultUploadTimeout,
}) {
  final container = ProviderContainer(overrides: [
    tokenManagerProvider.overrideWith((ref) => _Tokens(ref)),
    knowledgeCenterProvider.overrideWith((ref) => KnowledgeCenter(
          ref,
          httpClient: client,
          ingestTimeout: ingestTimeout,
          uploadTimeout: uploadTimeout,
        )),
  ]);
  addTearDown(container.dispose);
  return container;
}

/// Reads the view model and lets its initial refresh finish.
Future<KnowledgeViewModel> _viewModel(ProviderContainer container) async {
  container.listen(knowledgeViewModelProvider, (_, _) {});
  final vm = container.read(knowledgeViewModelProvider.notifier);
  await Future<void>.delayed(const Duration(milliseconds: 20));
  return vm;
}

void main() {
  group('INT-016 list paging', () {
    test('lists all 60 documents across pages, never asking for more than 100', () async {
      final limits = <int>[];
      final client = MockClient((request) async {
        limits.add(int.parse(request.url.queryParameters['limit'] ?? '50'));
        return _libraryPage(request, 60);
      });
      final docs = await _container(client).read(knowledgeCenterProvider).listDocuments();

      expect(docs, hasLength(60));
      expect(docs.map((d) => d.id).toSet(), hasLength(60));
      expect(limits, everyElement(lessThanOrEqualTo(100)));
    });

    test('pages past 100 too', () async {
      final client = MockClient((request) async => _libraryPage(request, 230));
      final docs = await _container(client).read(knowledgeCenterProvider).listDocuments();
      expect(docs, hasLength(230));
    });
  });

  group('INT-017 timeouts and idempotency', () {
    test('ingest allows long enough for synchronous embedding', () {
      expect(KnowledgeCenter.defaultIngestTimeout, greaterThanOrEqualTo(const Duration(seconds: 60)));
      expect(KnowledgeCenter.defaultUploadTimeout, greaterThanOrEqualTo(const Duration(seconds: 60)));
    });

    test('a retry of the same ingest after a timeout reuses its Idempotency-Key', () async {
      final keys = <String?>[];
      final never = Completer<http.Response>();
      var posts = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return request.url.path.endsWith('/cag') ? _cag() : _libraryPage(request, 0);
        }
        keys.add(request.headers[idempotencyKeyHeader]);
        posts++;
        if (posts == 1) return never.future; // the first attempt outlives the timeout
        return http.Response(jsonEncode({'document_id': 'd1', 'chunks': 1, 'created': true}), 200);
      });
      final container = _container(client, ingestTimeout: const Duration(milliseconds: 50));
      final vm = await _viewModel(container);

      await vm.ingest(title: 'Notes', content: 'hello');
      expect(container.read(knowledgeViewModelProvider).ingestError, isTrue);

      await vm.ingest(title: 'Notes', content: 'hello');
      expect(container.read(knowledgeViewModelProvider).ingestError, isFalse);
      expect(keys, hasLength(2));
      expect(keys[0], isNotNull);
      expect(keys[1], keys[0], reason: 'same logical ingest, same key');

      await vm.ingest(title: 'Notes', content: 'something else');
      expect(keys, hasLength(3));
      expect(keys[2], isNot(keys[0]), reason: 'a different document is a new write');
    });

    test('a PDF upload that stalls times out instead of hanging', () async {
      final never = Completer<http.Response>();
      final client = MockClient((request) => never.future);
      final result = await _container(client, uploadTimeout: const Duration(milliseconds: 50))
          .read(knowledgeCenterProvider)
          .uploadPdf(title: 't', bytes: [1, 2, 3])
          .timeout(const Duration(seconds: 5));
      expect(result.ok, isFalse);
      expect(result.failure, KnowledgeIngestFailure.timeout);
    });
  });

  group('INT-021 outcome messages', () {
    Future<String?> ingestAnswered(http.Response answer) async {
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return request.url.path.endsWith('/cag') ? _cag() : _libraryPage(request, 0);
        }
        return answer;
      });
      final container = _container(client);
      final vm = await _viewModel(container);
      await vm.ingest(title: 'Notes', content: 'hello');
      return container.read(knowledgeViewModelProvider).ingestMessage;
    }

    test('each failure gets its own message', () async {
      final tooLarge = await ingestAnswered(http.Response(
        jsonEncode({
          'error': 'validation_error',
          'message': 'document exceeds the configured size limit',
          'retryable': false,
          'details': {'limit_bytes': 8 * 1024 * 1024},
        }),
        422,
      ));
      final limited = await ingestAnswered(http.Response('Too Many Requests', 429));
      final unavailable = await ingestAnswered(http.Response(
        jsonEncode({'error': 'dependency_unavailable', 'message': 'x', 'retryable': true}),
        503,
      ));

      expect(tooLarge, contains('8 MB'));
      expect({tooLarge, limited, unavailable}, hasLength(3));
      expect(limited, isNot(contains('Could not add')));
    });

    test('an unchanged document is not reported as "Saved."', () async {
      final message = await ingestAnswered(http.Response(
        jsonEncode({
          'document_id': 'd1',
          'chunks': 0,
          'created': false,
          'quarantined_chunks': 0,
          'skipped_reason': 'unchanged_content',
        }),
        200,
      ));
      expect(message, isNot(startsWith('Saved')));
      expect(message, contains('unchanged'));
    });

    test('a fully quarantined document is an error, not "Saved."', () async {
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return request.url.path.endsWith('/cag') ? _cag() : _libraryPage(request, 0);
        }
        return http.Response(
          jsonEncode({
            'document_id': null,
            'chunks': 0,
            'created': false,
            'quarantined_chunks': 3,
            'skipped_reason': 'fully_quarantined',
          }),
          200,
        );
      });
      final container = _container(client);
      final vm = await _viewModel(container);
      await vm.ingest(title: 'Notes', content: 'hello');
      final state = container.read(knowledgeViewModelProvider);
      expect(state.ingestError, isTrue);
      expect(state.ingestMessage, isNot(startsWith('Saved')));
    });

    test('a PDF over the 8 MB limit is refused before it is sent', () async {
      var uploads = 0;
      final client = MockClient((request) async {
        if (request.method == 'GET') {
          return request.url.path.endsWith('/cag') ? _cag() : _libraryPage(request, 0);
        }
        uploads++;
        return http.Response('{}', 200);
      });
      final container = _container(client);
      final vm = await _viewModel(container);
      await vm.uploadPdf(
        title: 'big',
        bytes: List<int>.filled(KnowledgeCenter.defaultMaxDocumentBytes + 1, 0),
      );

      final state = container.read(knowledgeViewModelProvider);
      expect(uploads, 0);
      expect(state.ingestError, isTrue);
      expect(state.ingestMessage, contains('8 MB'));
    });

    // The limit is the server's setting (`KNOWLEDGE_MAX_DOC_BYTES`), advertised at
    // `/capabilities` `knowledge.max_upload_bytes`. A fixed 8 MiB would refuse files a server
    // configured higher accepts.
    http.Response capabilities(int maxUploadBytes) => http.Response(
          jsonEncode({
            'vision': {'enabled': false},
            'knowledge': {'max_upload_bytes': maxUploadBytes},
          }),
          200,
        );

    test('a server that allows more than 8 MB gets the larger PDF', () async {
      var uploads = 0;
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/api/v3/capabilities')) {
          return capabilities(16 * 1024 * 1024);
        }
        if (request.method == 'GET') {
          return request.url.path.endsWith('/cag') ? _cag() : _libraryPage(request, 0);
        }
        uploads++;
        return http.Response(jsonEncode({'document_id': 'd1', 'status': 'active'}), 200);
      });
      final container = _container(client);
      final vm = await _viewModel(container);
      await vm.uploadPdf(
        title: 'big but allowed',
        bytes: List<int>.filled(KnowledgeCenter.defaultMaxDocumentBytes + 1, 0),
      );

      expect(uploads, 1);
      expect(container.read(knowledgeViewModelProvider).ingestError, isFalse);
    });

    test('a server that allows less is checked before sending', () async {
      var posts = 0;
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/api/v3/capabilities')) return capabilities(1024);
        if (request.method == 'GET') {
          return request.url.path.endsWith('/cag') ? _cag() : _libraryPage(request, 0);
        }
        posts++;
        return http.Response('{}', 200);
      });
      final container = _container(client);
      final center = container.read(knowledgeCenterProvider);
      final result = await center.ingestDocumentResult(title: 't', content: 'x' * 2048);

      expect(posts, 0);
      expect(result.failure, KnowledgeIngestFailure.tooLarge);
      expect(result.limitBytes, 1024);
    });

    test('when /capabilities cannot be read, the documented default applies', () async {
      var uploads = 0;
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/api/v3/capabilities')) {
          return http.Response('upstream unavailable', 503);
        }
        if (request.method == 'GET') {
          return request.url.path.endsWith('/cag') ? _cag() : _libraryPage(request, 0);
        }
        uploads++;
        return http.Response('{}', 200);
      });
      final container = _container(client);
      final center = container.read(knowledgeCenterProvider);
      final result = await center.uploadPdf(
        title: 'big',
        bytes: List<int>.filled(KnowledgeCenter.defaultMaxDocumentBytes + 1, 0),
      );

      expect(uploads, 0);
      expect(result.failure, KnowledgeIngestFailure.tooLarge);
      expect(result.limitBytes, KnowledgeCenter.defaultMaxDocumentBytes);
    });

    test('a failed delete is reported, not thrown', () async {
      final client = MockClient((request) async {
        if (request.method == 'DELETE') {
          return http.Response(
            jsonEncode({'error': 'forbidden', 'message': 'not yours', 'retryable': false}),
            403,
          );
        }
        return request.url.path.endsWith('/cag') ? _cag() : _libraryPage(request, 1);
      });
      final container = _container(client);
      final vm = await _viewModel(container);

      await vm.remove('doc-0');

      final state = container.read(knowledgeViewModelProvider);
      expect(state.ingestError, isTrue);
      expect(state.ingestMessage, isNotNull);
      expect(state.documents, hasLength(1), reason: 'the list is still shown');
    });
  });
}
