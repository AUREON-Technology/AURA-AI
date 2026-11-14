// In-app account deletion (Apple 5.1.1(v), Google Play): the client half of
// `POST /api/v3/account/delete` (aura_backend/api/account.py).

import 'dart:convert';

import 'package:aura_client/core/auth/account_client.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/undo/undoable_deletes.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/features/auth/presentation/delete_account_confirmation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);
  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

class _FakeAuth extends AuthNotifier {
  _FakeAuth(this.outcome);
  final Future<AccountDeletionResult> Function() outcome;
  int deletes = 0;

  @override
  Future<UserProfile?> build() async =>
      UserProfile(id: 'user-1', email: 'a@example.com', displayName: 'A');

  @override
  Future<AccountDeletionResult> deleteAccount() async {
    deletes++;
    return outcome();
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  final requests = <http.Request>[];
  late http.Response Function(http.Request) respond;

  setUp(() {
    requests.clear();
    respond = (_) => http.Response(jsonEncode({'forgotten': true, 'failed': [], 'counts': {}}), 200);
  });

  ProviderContainer scope() => ProviderContainer(overrides: [
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        accountClientProvider.overrideWith((ref) => AccountClient(ref, httpClient: MockClient((r) async {
              requests.add(r);
              return respond(r);
            }))),
      ]);

  group('AccountClient.deleteAccount', () {
    test('posts the confirmation phrase with the bearer token', () async {
      final container = scope();
      addTearDown(container.dispose);

      final result = await container.read(accountClientProvider).deleteAccount();

      expect(result.forgotten, isTrue);
      expect(requests, hasLength(1));
      final request = requests.single;
      expect(request.method, 'POST');
      expect(request.url.path, '/api/v3/account/delete');
      expect(request.headers['Authorization'], 'Bearer jwt-test');
      expect(jsonDecode(request.body), {'confirm': 'DELETE'});
    });

    test('reports a partial erasure instead of claiming success', () async {
      respond = (_) => http.Response(
            jsonEncode({'forgotten': false, 'failed': ['documents'], 'counts': {}}), 200);
      final container = scope();
      addTearDown(container.dispose);

      final result = await container.read(accountClientProvider).deleteAccount();

      expect(result.forgotten, isFalse);
      expect(result.failed, ['documents']);
    });

    test('throws on a refusal, so nothing local is cleared', () async {
      respond = (_) => http.Response('{"detail":"cannot forget the last SuperAdmin"}', 403);
      final container = scope();
      addTearDown(container.dispose);

      await expectLater(container.read(accountClientProvider).deleteAccount(), throwsA(isA<http.ClientException>()));
    });
  });

  test('discardAll drops pending deletes without running them', () async {
    final container = ProviderContainer();
    addTearDown(container.dispose);
    var ran = 0;
    final deletes = container.read(undoableDeletesProvider.notifier)
      ..hide('task:1', () async => ran++)
      ..hide('task:2', () async => ran++);

    deletes.discardAll();
    await deletes.commitAll();

    expect(ran, 0);
    expect(container.read(undoableDeletesProvider), isEmpty);
  });

  testWidgets('the tile asks first, and cancelling deletes nothing', (tester) async {
    final container = scope();
    addTearDown(container.dispose);
    // A phone, not the 800x600 default: the dialog's buttons must fit at 360 px.
    tester.view.physicalSize = const Size(360, 740);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(UncontrolledProviderScope(
      container: container,
      child: MaterialApp(
        theme: AuraThemeV2.light,
        home: const Scaffold(body: DeleteAccountTile()),
      ),
    ));

    await tester.tap(find.text('Delete account'));
    await tester.pumpAndSettle();
    expect(find.text('Delete your account?'), findsOneWidget);
    expect(find.textContaining('cannot be undone'), findsOneWidget);

    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();

    expect(find.text('Delete your account?'), findsNothing);
    expect(requests, isEmpty, reason: 'cancel must not reach the server');
  });

  Future<_FakeAuth> pumpTile(WidgetTester tester, Future<AccountDeletionResult> Function() outcome) async {
    tester.view.physicalSize = const Size(360, 740);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final auth = _FakeAuth(outcome);
    await tester.pumpWidget(ProviderScope(
      overrides: [authProvider.overrideWith(() => auth)],
      child: MaterialApp(
        theme: AuraThemeV2.light,
        home: const Scaffold(body: DeleteAccountTile()),
      ),
    ));
    await tester.tap(find.text('Delete account'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(AuraButton, 'Delete'));
    await tester.pumpAndSettle();
    return auth;
  }

  testWidgets('confirming deletes and says so', (tester) async {
    final auth = await pumpTile(tester, () async => const AccountDeletionResult(forgotten: true));
    expect(auth.deletes, 1);
    expect(find.text('Your account has been deleted.'), findsOneWidget);
  });

  testWidgets('a partial erasure is reported as unfinished, not as done', (tester) async {
    await pumpTile(tester, () async => const AccountDeletionResult(forgotten: false, failed: ['documents']));
    expect(find.textContaining('could not be erased yet'), findsOneWidget);
    expect(find.text('Your account has been deleted.'), findsNothing);
  });

  testWidgets('a failed request says the account was not deleted', (tester) async {
    await pumpTile(tester, () async => throw http.ClientException('offline'));
    expect(find.textContaining('Your account was not deleted.'), findsOneWidget);
  });
}
