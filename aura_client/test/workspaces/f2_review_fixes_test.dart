// SaaS P1-F2 review regressions.

import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/core/routing/router.dart';
import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'package:aura_client/features/workspaces/data/workspaces_client.dart';
import 'package:aura_client/features/workspaces/presentation/team_settings_page.dart';
import 'package:flutter/material.dart';
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


void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('#2: the invite waits through onboarding', () async {
    expect(await resolveAppRedirect(isLoggedIn: true, onboarded: true, location: '/onboarding',
        desktop: false, hasPendingInvite: true), '/invite');
    expect(await resolveAppRedirect(isLoggedIn: true, onboarded: false, location: '/login',
        desktop: false, hasPendingInvite: true), '/onboarding');
  });

  group('API client', () {
    final requests = <http.Request>[];
    late http.Response Function(http.Request) respond;

    ProviderContainer scope() {
      requests.clear();
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

    test('#6: a request made while restoring waits for the saved team', () async {
      SharedPreferences.setMockInitialValues({ActiveWorkspace.prefsKey: jsonEncode(team().toJson())});
      respond = (_) => http.Response('{}', 200);
      final container = scope();
      await container.read(auraApiClientProvider).json(method: 'GET', path: '/api/v3/knowledge/documents');
      expect(requests.single.headers[workspaceHeaderName], 'ws-team');
    });

    test('#10: workspace routes carry no header and a 403 there does not drop the active team', () async {
      final container = scope();
      await container.read(activeWorkspaceProvider.notifier).select(team());
      respond = (_) => http.Response(jsonEncode({'error': 'authorization_error', 'message': notAMemberMessage}), 403);
      await expectLater(container.read(workspacesClientProvider).members('ws-other'), throwsA(anything));
      expect(requests.single.headers.containsKey(workspaceHeaderName), isFalse);
      expect(container.read(activeWorkspaceProvider)?.id, 'ws-team');
    });

    test('#5: a changed role or a lost team is reconciled from the list', () async {
      final container = scope();
      await container.read(activeWorkspaceProvider.notifier).select(team(role: 'owner'));
      await container.read(activeWorkspaceProvider.notifier).reconcile([personal, team(role: 'admin')]);
      expect(container.read(activeWorkspaceProvider)?.role, 'admin');
      await container.read(activeWorkspaceProvider.notifier).reconcile([personal]);
      expect(container.read(activeWorkspaceProvider), isNull);
    });
  });

  testWidgets('#3: a refused leave stays on the page and keeps the team', (tester) async {
    late ProviderContainer container;
    await pumpScreen(tester, Consumer(builder: (context, ref, _) {
      container = ProviderScope.containerOf(context);
      return const TeamSettingsPage(workspaceId: 'ws-team');
    }), uid: 'amy', active: team(role: 'member'), setup: (f) {
      f.workspaces = [personal, team(role: 'member')];
      f.failWith = apiError(409, 'conflict', 'cannot leave');
    });
    await tester.tap(find.byKey(const Key('team-leave')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Leave team').last);
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('team-leave')), findsOneWidget);
    expect(container.read(activeWorkspaceProvider)?.id, 'ws-team');
  });
}
