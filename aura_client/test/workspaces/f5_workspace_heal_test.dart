// F5: only `AuraApiClient` dropped a team the server says we no longer belong to. Chat streaming
// and the knowledge client sent the same `X-Aura-Workspace` and failed the same way, forever.
import 'dart:convert';

import 'package:aura_client/core/ai/knowledge/knowledge_center.dart';
import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'workspace_fakes.dart';

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);
  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

final _notAMember = jsonEncode({'error': 'authorization_error', 'message': notAMemberMessage});

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() => SharedPreferences.setMockInitialValues({}));

  late List<http.BaseRequest> requests;

  Future<ProviderContainer> inTeam(http.Client client) async {
    requests = [];
    final container = ProviderContainer(overrides: [
      tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
      knowledgeCenterProvider.overrideWith((ref) => KnowledgeCenter(ref, httpClient: client)),
      sseAgentClientProvider.overrideWith((ref) => SseAgentClient(ref, httpClient: client)),
    ]);
    addTearDown(container.dispose);
    await container.read(activeWorkspaceProvider.notifier).select(team());
    return container;
  }

  // The upload-limit lookup answers normally, so the refusal under test is the call's own.
  MockClient refusing() => MockClient((request) async {
        if (request.url.path.endsWith('/capabilities')) return http.Response('{}', 200);
        requests.add(request);
        return http.Response(_notAMember, 403);
      });

  test('a knowledge read refused as not-a-member drops the team', () async {
    final container = await inTeam(refusing());
    await expectLater(container.read(knowledgeCenterProvider).listDocuments(), throwsA(isA<AuraApiError>()));
    expect(requests.single.headers[workspaceHeaderName], 'ws-team');
    expect(container.read(activeWorkspaceProvider), isNull);
  });

  test('a knowledge write refused as not-a-member drops the team', () async {
    final container = await inTeam(refusing());
    await container.read(knowledgeCenterProvider).ingestDocumentResult(content: 'text', title: 't');
    expect(container.read(activeWorkspaceProvider), isNull);
  });

  test('a PDF upload refused as not-a-member drops the team', () async {
    final container = await inTeam(refusing());
    await container
        .read(knowledgeCenterProvider)
        .uploadPdf(bytes: [37, 80, 68, 70], filename: 'a.pdf', title: 'a');
    expect(container.read(activeWorkspaceProvider), isNull);
  });

  test('a chat turn refused as not-a-member drops the team', () async {
    final container = await inTeam(refusing());
    await expectLater(
      container.read(sseAgentClientProvider).streamAgentEvents(message: 'hi', sessionId: 's', agentId: 'a').toList(),
      throwsA(isA<AuraApiError>()),
    );
    expect(requests.single.headers[workspaceHeaderName], 'ws-team');
    expect(container.read(activeWorkspaceProvider), isNull);
  });

  test('any other 403 keeps the team', () async {
    final container = await inTeam(MockClient((request) async {
      requests.add(request);
      return http.Response('{"error":"authorization_error","message":"forbidden"}', 403);
    }));
    await expectLater(container.read(knowledgeCenterProvider).listDocuments(), throwsA(isA<AuraApiError>()));
    expect(container.read(activeWorkspaceProvider)?.id, 'ws-team');
  });

  // INT-111: the server answers 404 `not_found` for a document that is unknown, already deleted
  // or not ours. From the caller's view it is gone, so a retried delete is not an error.
  group('deleteDocument', () {
    test('a 404 is a successful delete', () async {
      final container = await inTeam(MockClient((request) async {
        requests.add(request);
        return http.Response('{"error":"not_found","message":"document not found"}', 404);
      }));
      await container.read(knowledgeCenterProvider).deleteDocument('doc-1');
      expect(requests.single.method, 'DELETE');
      expect(container.read(activeWorkspaceProvider)?.id, 'ws-team');
    });

    test('a not-a-member 403 still fails and drops the team', () async {
      final container = await inTeam(refusing());
      await expectLater(container.read(knowledgeCenterProvider).deleteDocument('doc-1'),
          throwsA(isA<AuraApiError>().having((e) => e.statusCode, 'statusCode', 403)));
      expect(container.read(activeWorkspaceProvider), isNull);
    });

    test('a server error is still an error', () async {
      final container = await inTeam(MockClient((request) async => http.Response('{}', 500)));
      await expectLater(container.read(knowledgeCenterProvider).deleteDocument('doc-1'), throwsA(isA<AuraApiError>()));
    });
  });
}
