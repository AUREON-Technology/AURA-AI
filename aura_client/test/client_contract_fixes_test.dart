// Client halves of the 2026-11-17 integration audit fixes: INT-018, 023, 024, 025, 026, 028.
//
// Each test drives the real class against a MockClient, the same way the other client tests
// do, so the assertions are about what goes over the wire and what the caller gets back.

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:aura_client/core/ai/ai_router/aura_ai_router.dart';
import 'package:aura_client/core/ai/engineer/engineer_workspaces.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/insights/insights_client.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/features/automations/data/automation_repository.dart';
import 'package:aura_client/features/chat/data/agents_repository.dart';
import 'package:aura_client/features/engineering/data/engineer_run_repository.dart';
import 'package:aura_client/features/engineering/data/github_integration_repository.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _CountingTokenManager extends TokenManager {
  _CountingTokenManager(super.ref);
  int invalidations = 0;

  @override
  Future<String?> getValidAccessToken() async => 'jwt-$invalidations';

  @override
  void invalidateAccessToken() => invalidations++;
}

/// Answers 401 to the first request and [then] to every later one, recording all of them.
class _FirstCallUnauthorized {
  _FirstCallUnauthorized(this.then);
  final http.Response Function(http.Request request) then;
  final List<http.Request> requests = [];

  http.Client client() => MockClient((request) async {
        requests.add(request);
        if (requests.length == 1) return http.Response('{"error":"authentication_error"}', 401);
        return then(request);
      });
}

ProviderContainer _scope(http.Client httpClient, {Duration? timeout}) {
  final container = ProviderContainer(
    // Riverpod 3 retries a failed provider by default; the error itself is what is under test.
    retry: (_, _) => null,
    overrides: [
      tokenManagerProvider.overrideWith((ref) => _CountingTokenManager(ref)),
      auraApiClientProvider.overrideWith(
        (ref) => timeout == null
            ? AuraApiClient(ref, httpClient: httpClient)
            : AuraApiClient(ref, httpClient: httpClient, timeout: timeout, healthTimeout: timeout),
      ),
      insightsClientProvider.overrideWith(
        (ref) => InsightsClient(ref, httpClient: httpClient, timezone: () async => null),
      ),
      engineerWorkspacesClientProvider.overrideWith(
        (ref) => EngineerWorkspacesClient(ref, httpClient: httpClient),
      ),
      databaseProvider.overrideWith((ref) => throw StateError('no database in this test')),
    ],
  );
  addTearDown(container.dispose);
  return container;
}

void _expectAuthenticatedRetry(List<http.Request> requests) {
  expect(requests, hasLength(2), reason: 'one 401, then exactly one retry');
  for (final request in requests) {
    expect(request.headers['X-Device-Platform'], isNotNull, reason: 'attestation headers');
    expect(request.headers['X-Device-Attestation'], isNotNull);
  }
  expect(requests[0].headers['Authorization'], 'Bearer jwt-0');
  expect(requests[1].headers['Authorization'], 'Bearer jwt-1',
      reason: 'the retry re-reads the token after invalidating it');
}

final _hang = MockClient((request) => Completer<http.Response>().future);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('INT-028: AuraApiError formats FastAPI detail', () {
    test('a string detail becomes the message, not raw JSON', () {
      final error = AuraApiError.fromResponse(statusCode: 404, body: '{"detail":"Not Found"}');
      expect(error.message, 'Not Found');
      expect(error.error, 'http_404');
    });

    test('a validation list becomes a readable sentence', () {
      final error = AuraApiError.fromResponse(
        statusCode: 422,
        body: jsonEncode({
          'detail': [
            {'loc': ['body', 'title'], 'msg': 'Field required', 'type': 'missing'},
            {'loc': ['query', 'limit'], 'msg': 'Input should be less than or equal to 100'},
          ],
        }),
      );
      expect(error.error, 'validation_error');
      expect(error.message,
          'title: Field required; limit: Input should be less than or equal to 100');
      expect(error.message, isNot(contains('{')));
    });

    test('a structured detail map still wins', () {
      final error = AuraApiError.fromResponse(
        statusCode: 503,
        body: '{"detail":{"error":"analysis_unavailable","reason":"invalid_output"}}',
      );
      expect(error.error, 'analysis_unavailable');
      expect(error.message, isNot(contains('{')));
    });
  });

  group('INT-028: timeouts', () {
    test('a request that never answers becomes an unreachable error', () async {
      final container = _scope(_hang, timeout: const Duration(milliseconds: 50));
      final api = container.read(auraApiClientProvider);
      await expectLater(
        api.json(method: 'GET', path: '/api/v3/agents'),
        throwsA(isA<AuraApiError>().having((e) => e.error, 'error', 'unreachable')),
      );
    });

    test('probeGateway reports offline instead of hanging', () async {
      final container = _scope(_hang, timeout: const Duration(milliseconds: 50));
      final ok = await container
          .read(aiRouter)
          .probeGateway()
          .timeout(const Duration(seconds: 5), onTimeout: () => fail('probe hung'));
      expect(ok, isFalse);
      expect(container.read(aiRouterStatusProvider).isOffline, isTrue);
    });
  });

  group('INT-023: insight dismiss', () {
    test('retries once on 401 and reports success', () async {
      final server = _FirstCallUnauthorized((_) => http.Response('{"status":"dismissed"}', 200));
      final container = _scope(server.client());
      expect(await container.read(insightsClientProvider).dismiss('i-1'), isTrue);
      _expectAuthenticatedRetry(server.requests);
      expect(server.requests.last.url.path, '/api/v3/insights/i-1/dismiss');
    });

    test('a failed dismiss is reported, not swallowed', () async {
      final container = _scope(MockClient((_) async => http.Response('nope', 500)));
      expect(await container.read(insightsClientProvider).dismiss('i-1'), isFalse);
    });
  });

  group('INT-024: shared client pattern', () {
    test('agents: attestation and 401 retry', () async {
      final server = _FirstCallUnauthorized(
          (_) => http.Response('{"agents":[{"id":"orchestrator"}]}', 200));
      final container = _scope(server.client());
      final agents = await container.read(agentsProvider.future);
      expect(agents.single.id, 'orchestrator');
      _expectAuthenticatedRetry(server.requests);
    });

    test('engineer runs: attestation and 401 retry', () async {
      final server = _FirstCallUnauthorized((_) => http.Response('{"runs":[]}', 200));
      final container = _scope(server.client());
      expect(await container.read(engineerRunsProvider.future), isEmpty);
      _expectAuthenticatedRetry(server.requests);
    });

    test('github status, connect and disconnect: attestation and 401 retry', () async {
      final status = _FirstCallUnauthorized((_) => http.Response('{"connected":true}', 200));
      var container = _scope(status.client());
      expect((await container.read(githubConnectionStatusProvider.future)).connected, isTrue);
      _expectAuthenticatedRetry(status.requests);

      final connect = _FirstCallUnauthorized(
          (_) => http.Response('{"authorize_url":"https://github.com/login"}', 200));
      container = _scope(connect.client());
      expect(await fetchGithubAuthorizeUrl(container.read(auraApiClientProvider)),
          'https://github.com/login');
      _expectAuthenticatedRetry(connect.requests);

      final disconnect = _FirstCallUnauthorized((_) => http.Response('{}', 200));
      container = _scope(disconnect.client());
      await disconnectGithub(container.read(auraApiClientProvider));
      _expectAuthenticatedRetry(disconnect.requests);
      expect(disconnect.requests.last.method, 'POST');
    });

    test('a server error surfaces as an AuraApiError, not a bare Exception', () async {
      final container = _scope(MockClient((_) async => http.Response('{"detail":"boom"}', 500)));
      // autoDispose: hold a listener so the provider is not torn down mid-request.
      final runs = container.listen(engineerRunsProvider.future, (_, _) {});
      await expectLater(
        runs.read(),
        throwsA(isA<AuraApiError>().having((e) => e.statusCode, 'status', 500)),
      );
    });
  });

  group('INT-025: engineer workspaces', () {
    test('a disabled deployment is "not enabled"', () async {
      final container = _scope(MockClient(
          (_) async => http.Response('{"workspaces":[],"enabled":false}', 200)));
      final list = await container.read(engineerWorkspacesClientProvider).list();
      expect(list.enabled, isFalse);
    });

    test('a server error is an error, not "not enabled"', () async {
      final container = _scope(MockClient((_) async => http.Response('oops', 500)));
      await expectLater(
        container.read(engineerWorkspacesClientProvider).list(),
        throwsA(isA<AuraApiError>()),
      );
    });

    test('a malformed body is an error, not "not enabled"', () async {
      final container = _scope(MockClient(
          (_) async => http.Response('{"enabled":true,"workspaces":[{"id":1}]}', 200)));
      await expectLater(
        container.read(engineerWorkspacesClientProvider).list(),
        throwsA(isA<FormatException>()),
      );
    });

    test('a 401 is retried once with attestation', () async {
      final server = _FirstCallUnauthorized(
          (_) => http.Response('{"workspaces":[],"enabled":true,"ttl_hours":24}', 200));
      final container = _scope(server.client());
      final list = await container.read(engineerWorkspacesClientProvider).list();
      expect(list.enabled, isTrue);
      expect(list.ttlHours, 24);
      _expectAuthenticatedRetry(server.requests);
    });
  });

  group('INT-026: per-request image total', () {
    test('capabilities parse max_request_image_bytes', () {
      final caps = VisionCapabilities.fromJson({
        'vision': {
          'enabled': true, 'available': true, 'max_images': 4,
          'max_image_bytes': 100, 'max_request_image_bytes': 150,
        },
      });
      expect(caps.maxRequestImageBytes, 150);
    });

    test('an image that would push the turn over the total is refused', () {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      const limits = VisionCapabilities(
        enabled: true, available: true, maxImages: 4, maxImageBytes: 100,
        maxRequestImageBytes: 150);
      final pending = container.read(pendingChatImagesProvider.notifier);
      ChatImageAttachment image(int size) =>
          ChatImageAttachment(bytes: Uint8List(size), name: 'a.png');

      expect(pending.add(image(80), limits), isNull);
      expect(pending.add(image(80), limits), AttachRejection.totalTooLarge);
      expect(pending.add(image(70), limits), isNull);
      expect(container.read(pendingChatImagesProvider), hasLength(2));
    });
  });

  group('INT-018: automation results parse defensively', () {
    test('wrong types do not throw', () {
      final result = EmailAnalysisResult.fromMap({
        'category': 7,
        'summary': null,
        'urgency': ['high'],
        'action_items': ['Revise', {'title': 3, 'due_date': 20261120}, 9],
        'should_create_event': 'true',
        'event_title': 12,
        'event_start': 1763600000,
        'event_end': '2026-11-20T11:00:00',
      });
      expect(result.category, 'General');
      expect(result.summary, '');
      expect(result.urgency, 'low');
      expect(result.actionItems.map((i) => i.title), ['Revise', '3']);
      expect(result.actionItems[1].dueDate, isNull);
      expect(result.shouldCreateEvent, isTrue);
      expect(result.eventTitle, '12');
      expect(result.eventStart, isNull);
      expect(result.eventEnd, DateTime(2026, 11, 20, 11));
    });

    test('issue subtasks tolerate a single string', () {
      final result = GitHubIssueResult.fromMap(
          {'summary': 's', 'subtasks': 'one thing', 'complexity': 2});
      expect(result.subtasks, ['one thing']);
      expect(result.complexity, 'medium');
    });
  });
}
