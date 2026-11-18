// SaaS P1-F2 Task 2: the workspace switcher and team settings.

import 'package:aura_client/core/workspaces/active_workspace.dart';
import 'package:aura_client/features/workspaces/presentation/team_settings_page.dart';
import 'package:aura_client/features/workspaces/presentation/workspace_switcher.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'workspace_fakes.dart';

void _as(FakeWorkspaces f, String role) => f.workspaces = [personal, team(role: role)];

/// A page with a button that opens the switcher as a real sheet, as the app does.
class _Opener extends StatelessWidget {
  const _Opener();

  @override
  Widget build(BuildContext context) => Scaffold(
        body: Center(child: TextButton(onPressed: () => showWorkspaceSwitcher(context), child: const Text('open'))),
      );
}

Future<void> _openSwitcher(WidgetTester tester, {void Function(FakeWorkspaces)? setup}) async {
  await pumpScreen(tester, const _Opener(), setup: setup);
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('the switcher lists workspaces and selects one', (tester) async {
    await _openSwitcher(tester);
    expect(find.text('Personal'), findsOneWidget);
    expect(find.text('Acme'), findsOneWidget);
    final container = ProviderScope.containerOf(tester.element(find.byType(WorkspaceSwitcher)));
    await tester.tap(find.byKey(const Key('workspace-ws-team')));
    await tester.pumpAndSettle();
    expect(container.read(activeWorkspaceProvider)?.id, 'ws-team');
  });

  testWidgets('creating a team without a verified email explains why', (tester) async {
    await _openSwitcher(tester,
        setup: (f) => f.failWith = apiError(403, 'authorization_error',
            'verify your email address before creating or joining a team'));
    await tester.tap(find.text('Create a team'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('create-team-name')), 'Acme 2');
    await tester.tap(find.text('Create'));
    await tester.pumpAndSettle();
    expect(fake.calls, contains('create:Acme 2'));
    expect(find.byType(SnackBar), findsOneWidget);
  });

  testWidgets('the owner can invite, change roles and sees delete', (tester) async {
    await pumpScreen(tester, const TeamSettingsPage(workspaceId: 'ws-team'));
    expect(find.text('You'), findsOneWidget);
    expect(find.text('amy@example.com'), findsOneWidget);
    expect(find.text('dan@example.com'), findsOneWidget); // pending invite
    expect(find.byKey(const Key('member-menu-amy')), findsOneWidget);
    expect(find.byKey(const Key('member-menu-ada')), findsOneWidget);
    expect(find.byKey(const Key('team-delete')), findsOneWidget);

    await tester.tap(find.byKey(const Key('member-menu-amy')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Make admin'));
    await tester.pumpAndSettle();
    expect(fake.calls, contains('role:amy:admin'));

    await tester.tap(find.text('Invite'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('invite-email')), 'bea@example.com');
    await tester.tap(find.text('Invite').last);
    await tester.pumpAndSettle();
    expect(fake.calls, contains('invite:bea@example.com:member'));
    expect(find.byKey(const Key('invite-link')), findsOneWidget);
  });

  testWidgets('a seat limit shows the plan sentence', (tester) async {
    await pumpScreen(tester, const TeamSettingsPage(workspaceId: 'ws-team'),
        setup: (f) => f.failWith = apiError(402, 'plan_limit', 'this workspace has no free seats',
            {'limit': 'seats', 'plan': 'team', 'used': 3, 'allowed': 3}));
    await tester.tap(find.text('Invite'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('invite-email')), 'bea@example.com');
    await tester.tap(find.text('Invite').last);
    await tester.pumpAndSettle();
    expect(find.byType(SnackBar), findsOneWidget);
    expect(find.byKey(const Key('invite-link')), findsNothing);
  });

  testWidgets('a member sees the team read-only', (tester) async {
    await pumpScreen(tester, const TeamSettingsPage(workspaceId: 'ws-team'), uid: 'amy', setup: (f) => _as(f, 'member'));
    expect(find.byKey(const Key('team-read-only')), findsOneWidget);
    expect(find.text('Invite'), findsNothing);
    expect(find.byType(PopupMenuButton<String>), findsNothing);
    expect(find.byKey(const Key('team-delete')), findsNothing);
    expect(find.byKey(const Key('team-leave')), findsOneWidget);
    expect(find.text('Pending invites'), findsNothing);
  });

  testWidgets('an admin manages members but not other admins or the owner', (tester) async {
    await pumpScreen(tester, const TeamSettingsPage(workspaceId: 'ws-team'), uid: 'ada', setup: (f) => _as(f, 'admin'));
    expect(find.byKey(const Key('member-menu-amy')), findsOneWidget);
    expect(find.byKey(const Key('member-menu-own')), findsNothing);
    expect(find.byKey(const Key('team-delete')), findsNothing);
  });

  testWidgets('leaving asks first', (tester) async {
    await pumpScreen(tester, const TeamSettingsPage(workspaceId: 'ws-team'), uid: 'amy', setup: (f) => _as(f, 'member'));
    await tester.tap(find.byKey(const Key('team-leave')));
    await tester.pumpAndSettle();
    expect(find.textContaining("You'll lose access"), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(fake.calls.where((c) => c.startsWith('remove')), isEmpty);
  });
}
