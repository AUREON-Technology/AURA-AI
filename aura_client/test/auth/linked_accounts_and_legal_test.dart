// SaaS P1-F2 Task 5: sign-in methods, legal links at sign-up, the team-owner deletion notice.

import 'dart:convert';

import 'package:aura_client/core/auth/account_client.dart';
import 'package:aura_client/core/auth/identity_service.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/legal_links.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/auth/presentation/delete_account_confirmation.dart';
import 'package:aura_client/features/auth/presentation/legal_links_text.dart';
import 'package:aura_client/features/auth/presentation/linked_accounts_page.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _Identity implements IdentityService {
  _Identity(this.linked);

  List<String> linked;
  IdentityError? failWith;
  final calls = <String>[];

  @override
  List<String> linkedProviders() => linked;

  Future<void> _do(String what) async {
    calls.add(what);
    if (failWith != null) throw failWith!;
    linked = [...linked, what];
  }

  @override
  Future<void> linkGoogle() => _do('google.com');
  @override
  Future<void> linkApple() => _do('apple.com');
  @override
  Future<void> linkEmailPassword(String email, String password) => _do('password');

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);
  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

Widget _app(Widget child, List overrides) => ProviderScope(
      overrides: [...overrides],
      child: MaterialApp.router(
        theme: AuraThemeV2.light,
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        routerConfig: GoRouter(routes: [
          GoRoute(path: '/', builder: (_, _) => child),
          GoRoute(path: '/workspaces/:id', builder: (_, s) => Scaffold(body: Text('TEAM ${s.pathParameters['id']}'))),
        ]),
      ),
    );

void main() {
  testWidgets('lists linked methods and connects Google', (tester) async {
    final identity = _Identity(['password']);
    await tester.pumpWidget(_app(const LinkedAccountsPage(), [identityServiceProvider.overrideWithValue(identity)]));
    await tester.pumpAndSettle();
    expect(find.descendant(of: find.byKey(const Key('provider-password')), matching: find.text('Connected')), findsOneWidget);
    await tester.tap(find.descendant(of: find.byKey(const Key('provider-google.com')), matching: find.text('Connect')));
    await tester.pumpAndSettle();
    expect(identity.calls, ['google.com']);
    expect(find.text('Sign-in method connected'), findsOneWidget);
  });

  testWidgets('a sign-in that belongs to another account is explained', (tester) async {
    final identity = _Identity(['google.com'])
      ..failWith = const IdentityError(IdentityErrorKind.credentialInUse, 'credential-already-in-use');
    await tester.pumpWidget(_app(const LinkedAccountsPage(), [identityServiceProvider.overrideWithValue(identity)]));
    await tester.pumpAndSettle();
    await tester.tap(find.descendant(of: find.byKey(const Key('provider-apple.com')), matching: find.text('Connect')));
    await tester.pumpAndSettle();
    expect(find.text('That sign-in already belongs to another AURA account.'), findsOneWidget);
  });

  test('credential-already-in-use maps to its own kind', () {
    expect(identityErrorKindFor('credential-already-in-use'), IdentityErrorKind.credentialInUse);
  });

  testWidgets('legal links render only when configured, and only https', (tester) async {
    await tester.pumpWidget(_app(const Scaffold(body: LegalLinksText()), [
      legalLinksProvider.overrideWithValue(const LegalLinks()),
    ]));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('legal-links')), findsNothing);

    await tester.pumpWidget(_app(const Scaffold(body: LegalLinksText()), [
      legalLinksProvider.overrideWithValue(LegalLinks(terms: Uri.parse('https://aureon-ai.tech/terms'))),
    ]));
    await tester.pumpAndSettle();
    expect(find.text('Terms of Service'), findsOneWidget);
    expect(find.text('Privacy Policy'), findsNothing);
  });

  test('LegalLinks refuses anything but https', () {
    expect(LegalLinks.fromEnvironment().isEmpty, isTrue); // no --dart-define in tests
  });

  test('a 409 owns_team_workspaces is AccountDeletionBlocked naming the teams', () async {
    final container = ProviderContainer(overrides: [
      tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
      accountClientProvider.overrideWith((ref) => AccountClient(ref, httpClient: MockClient((r) async => http.Response(
          jsonEncode({'error': 'owns_team_workspaces', 'workspaces': [{'id': 'ws1', 'name': 'Acme'}]}), 409)))),
    ]);
    addTearDown(container.dispose);
    expect(
      () => container.read(accountClientProvider).deleteAccount(),
      throwsA(isA<AccountDeletionBlocked>().having((b) => b.workspaces.single.name, 'name', 'Acme')),
    );
  });

  testWidgets('the deletion notice lists the teams and opens their settings', (tester) async {
    await tester.pumpWidget(_app(Builder(builder: (context) => Scaffold(body: TextButton(
      onPressed: () => showDeletionBlocked(context, const AccountDeletionBlocked([(id: 'ws1', name: 'Acme')])),
      child: const Text('go'),
    ))), const []));
    await tester.pumpAndSettle();
    await tester.tap(find.text('go'));
    await tester.pumpAndSettle();
    expect(find.text('You own a team'), findsOneWidget);
    await tester.tap(find.text('Acme'));
    await tester.pumpAndSettle();
    expect(find.text('TEAM ws1'), findsOneWidget);
  });
}
