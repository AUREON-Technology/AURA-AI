// SaaS P1-F2 Task 4: accepting an invite and sharing chats with the team.

import 'package:aura_client/core/platform/aura_platform.dart';
import 'package:aura_client/core/routing/router.dart';
import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'package:aura_client/features/chat/data/chat_connection_error.dart';
import 'package:aura_client/features/workspaces/data/workspace_models.dart';
import 'package:aura_client/features/workspaces/presentation/invite_accept_page.dart';
import 'package:aura_client/features/workspaces/presentation/pending_invite.dart';
import 'package:aura_client/features/workspaces/presentation/share_with_team_button.dart';
import 'package:aura_client/features/workspaces/presentation/shared_chats_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'workspace_fakes.dart';


void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('signed in with a pending invite lands on /invite, not home', () async {
    expect(await resolveAppRedirect(isLoggedIn: true, onboarded: true, location: '/login',
        desktop: false, hasPendingInvite: true), '/invite');
    expect(await resolveAppRedirect(isLoggedIn: true, onboarded: true, location: '/login',
        desktop: false), landingRouteFor(desktop: false));
    expect(await resolveAppRedirect(isLoggedIn: false, onboarded: null, location: '/invite',
        desktop: false), '/login');
  });

  testWidgets('a held token is used once and never written to disk', (tester) async {
    await pumpScreen(tester, const InviteAcceptPage(), overrides: [
      pendingInviteProvider.overrideWith(_Held.new),
    ]);
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getKeys().any((k) => (prefs.get(k)?.toString() ?? '').contains('auraiv_secret')), isFalse);
    await tester.tap(find.byKey(const Key('invite-join')));
    await tester.pumpAndSettle();
    expect(fake.calls, contains('accept'));
  });

  testWidgets('accepting selects the team', (tester) async {
    late ProviderContainer container;
    await pumpScreen(tester, Consumer(builder: (context, ref, _) {
      container = ProviderScope.containerOf(context);
      return const InviteAcceptPage(token: 'auraiv_tok');
    }), routes: [GoRoute(path: '/workspaces/:id', builder: (_, _) => const Scaffold(body: Text('TEAM')))]);
    await tester.tap(find.byKey(const Key('invite-join')));
    await tester.pumpAndSettle();
    expect(container.read(activeWorkspaceProvider)?.id, 'ws-team');
    expect(find.text('TEAM'), findsOneWidget);
  });

  testWidgets('an expired invite and an unverified email are explained', (tester) async {
    await pumpScreen(tester, const InviteAcceptPage(token: 'auraiv_old'),
        setup: (f) => f.failWith = apiError(409, 'conflict', 'this invite is no longer valid'));
    await tester.tap(find.byKey(const Key('invite-join')));
    await tester.pumpAndSettle();
    expect(find.text('This invite is no longer valid. Ask for a new one.'), findsOneWidget);

    await pumpScreen(tester, const InviteAcceptPage(token: 'auraiv_tok'),
        setup: (f) => f.failWith = apiError(403, 'authorization_error', 'verify your email address'));
    await tester.tap(find.byKey(const Key('invite-join')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Verify your email address first'), findsOneWidget);
  });

  testWidgets('no token says the invite is not valid', (tester) async {
    await pumpScreen(tester, const InviteAcceptPage());
    expect(find.byKey(const Key('invite-problem')), findsOneWidget);
    expect(find.byKey(const Key('invite-join')), findsNothing);
  });

  test('only real user and assistant text is shared', () {
    final messages = shareableMessages([
      (role: 'user', text: 'How do we deploy?'),
      (role: 'assistant', text: 'With Railway.'),
      (role: 'assistant', text: kChatConnectionErrorMessage),
      (role: 'assistant', text: '$kChatPlanLimitPrefix: no messages left'),
      (role: 'system', text: 'internal'),
      (role: 'user', text: '   '),
    ]);
    expect([for (final m in messages) m.text], ['How do we deploy?', 'With Railway.']);
    expect(shareTitle(messages), 'How do we deploy?');
  });

  testWidgets('the share button only shows in a team', (tester) async {
    await pumpScreen(tester, const Scaffold(body: ShareWithTeamButton()));
    expect(find.byKey(const Key('share-with-team')), findsNothing);
    await pumpScreen(tester, const Scaffold(body: ShareWithTeamButton()), active: team());
    expect(find.byKey(const Key('share-with-team')), findsOneWidget);
  });

  testWidgets('team chats list and open; unshare only for the sharer or an admin', (tester) async {
    final chats = [SharedChatSummary(id: 's1', title: 'Deploys', sharedBy: 'amy'),
                   SharedChatSummary(id: 's2', title: 'Mine', sharedBy: 'bob')];
    final routes = [GoRoute(path: '/team-chats/:sid', builder: (_, s) => SharedChatView(sharedId: s.pathParameters['sid']!))];
    await pumpScreen(tester, const SharedChatsPage(), uid: 'bob', active: team(role: 'member'),
        routes: routes, setup: (f) => f.sharedList = chats);
    expect(find.text('Deploys'), findsOneWidget);
    await tester.tap(find.text('Deploys'));
    await tester.pumpAndSettle();
    expect(find.text('How do we deploy?'), findsOneWidget);
    expect(find.byKey(const Key('unshare')), findsNothing); // amy's, and bob is a member

    await pumpScreen(tester, const SharedChatsPage(), uid: 'bob', active: team(role: 'member'),
        routes: routes, setup: (f) => f.sharedList = chats);
    await tester.tap(find.text('Mine'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('unshare')), findsOneWidget);
    await tester.tap(find.byKey(const Key('unshare')));
    await tester.pumpAndSettle();
    expect(fake.calls, contains('unshare:s2'));
  });

  testWidgets('an empty team says so', (tester) async {
    await pumpScreen(tester, const SharedChatsPage(), active: team());
    expect(find.byKey(const Key('team-chats-empty')), findsOneWidget);
  });
}


class _Held extends PendingInvite {
  @override
  String? build() => 'auraiv_secret';
}
