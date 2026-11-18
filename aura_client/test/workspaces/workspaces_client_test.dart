// SaaS P1-F2 Task 1: the workspaces client, the active workspace and its header.

import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'package:aura_client/features/workspaces/data/workspace_models.dart';
import 'package:aura_client/features/workspaces/data/workspaces_client.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);
  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

const _team = WorkspaceSummary(id: 'ws-team', kind: 'team', name: 'Acme', role: 'owner');
const _personal = WorkspaceSummary(id: 'ws-me', kind: 'personal', name: 'Personal', role: 'owner');

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  final requests = <http.Request>[];
  late http.Response Function(http.Request) respond;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    requests.clear();
    respond = (_) => http.Response('{}', 200);
  });

  ProviderContainer scope() {
    final container = ProviderContainer(overrides: [
      tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
      auraApiClientProvider.overrideWith((ref) => AuraApiClient(ref, httpClient: MockClient((r) async {
            requests.add(r);
            return respond(r);
          }))),
    ]);
    addTearDown(container.dispose);
    return container;
  }

  test('list parses workspaces with a bearer', () async {
    respond = (_) => http.Response(jsonEncode({'workspaces': [_personal.toJson(), _team.toJson()]}), 200);
    final listed = await scope().read(workspacesClientProvider).list();
    expect(listed, [_personal, _team]);
    expect(requests.single.method, 'GET');
    expect(requests.single.url.path, '/api/v3/workspaces');
    expect(requests.single.headers['Authorization'], 'Bearer jwt-test');
  });

  test('each call hits its route', () async {
    respond = (r) {
      if (r.url.path.endsWith('/invites') && r.method == 'POST') {
        return http.Response(jsonEncode({
          'invite': {'invite_id': 'i1', 'email': 'bea@example.com', 'role': 'member'},
          'token': 'auraiv_tok',
        }), 201);
      }
      if (r.url.path.endsWith('/plan')) {
        return http.Response(jsonEncode({
          'plan': 'pro', 'seats': 1, 'messages_used': 3, 'messages_allowed': 2000,
          'storage_used': 10, 'storage_allowed': 100, 'engineer': true, 'resets_at': '2026-12-01T00:00:00+00:00',
        }), 200);
      }
      if (r.url.path.endsWith('/checkout')) {
        return http.Response(jsonEncode({'payment_id': 'p1', 'reference_number': '912345678', 'amount_piastres': 15000}), 201);
      }
      return http.Response('{}', 200);
    };
    final client = scope().read(workspacesClientProvider);
    final created = await client.invite('ws-team', 'bea@example.com', 'member');
    expect(created.token, 'auraiv_tok');
    expect(jsonDecode(requests.last.body), {'email': 'bea@example.com', 'role': 'member'});
    await client.changeRole('ws-team', 'u/2', 'admin');
    expect(requests.last.method, 'PATCH');
    expect(requests.last.url.path, '/api/v3/workspaces/ws-team/members/u%2F2');
    final usage = await client.plan('ws-team');
    expect((usage.plan, usage.messagesAllowed, usage.engineer), ('pro', 2000, true));
    final checkout = await client.checkout('ws-team', 'team', 3);
    expect((checkout.referenceNumber, formatEgp(checkout.amountPiastres)), ('912345678', 'EGP 150.00'));
    await client.share('ws-team', 'chat-1', 'Deploys', const [SharedChatMessage(role: 'user', text: 'hi')]);
    expect(jsonDecode(requests.last.body)['messages'], [{'role': 'user', 'text': 'hi'}]);
    await client.unshare('ws-team', 's1');
    expect((requests.last.method, requests.last.url.path), ('DELETE', '/api/v3/workspaces/ws-team/shared-conversations/s1'));
  });

  test('a refusal throws AuraApiError with the server details', () async {
    respond = (_) => http.Response(jsonEncode({
          'error': 'plan_limit', 'message': 'this workspace has no free seats',
          'details': {'limit': 'seats', 'plan': 'team', 'used': 3, 'allowed': 3},
        }), 402);
    expect(
      () => scope().read(workspacesClientProvider).invite('ws-team', 'x@example.com', 'member'),
      throwsA(isA<AuraApiError>()
          .having((e) => e.statusCode, 'status', 402)
          .having((e) => e.details['limit'], 'limit', 'seats')),
    );
  });

  test('a team sends the workspace header, personal does not', () async {
    final container = scope();
    await container.read(workspacesClientProvider).list();
    expect(requests.last.headers.containsKey(workspaceHeaderName), isFalse);
    await container.read(activeWorkspaceProvider.notifier).select(_personal);
    await container.read(workspacesClientProvider).list();
    expect(requests.last.headers.containsKey(workspaceHeaderName), isFalse);
    await container.read(activeWorkspaceProvider.notifier).select(_team);
    await container.read(workspacesClientProvider).list();
    expect(requests.last.headers[workspaceHeaderName], 'ws-team');
  });

  test('the selection survives a restart', () async {
    final first = scope();
    await first.read(activeWorkspaceProvider.notifier).select(_team);
    final second = scope();
    second.read(activeWorkspaceProvider);
    await second.read(activeWorkspaceProvider.notifier).restore();
    expect(second.read(activeWorkspaceProvider), _team);
  });

  test('a not-a-member 403 falls back to personal', () async {
    final container = scope();
    await container.read(activeWorkspaceProvider.notifier).select(_team);
    respond = (_) => http.Response(jsonEncode({'error': 'authorization_error', 'message': notAMemberMessage}), 403);
    await expectLater(container.read(workspacesClientProvider).members('ws-team'), throwsA(isA<AuraApiError>()));
    expect(container.read(activeWorkspaceProvider), isNull);
  });

  test('an owner-only 403 keeps the team', () async {
    final container = scope();
    await container.read(activeWorkspaceProvider.notifier).select(_team);
    respond = (_) => http.Response(jsonEncode({'error': 'authorization_error', 'message': 'only the workspace owner can buy a plan'}), 403);
    await expectLater(container.read(workspacesClientProvider).checkout('ws-team', 'team', 2), throwsA(isA<AuraApiError>()));
    expect(container.read(activeWorkspaceProvider), _team);
  });
}
