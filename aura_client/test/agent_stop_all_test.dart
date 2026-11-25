// "Stop all agent activity" (ADR-041, inventory row 100): the kill switch existed on the server but
// no screen could press it. The repository and the real AuraApiClient run against a MockClient that
// answers like the gateway (`POST /api/v3/agents/stop-all`); the page test drives the real dialog.

import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/features/engineering/data/run_history_repository.dart';
import 'package:aura_client/features/engineering/presentation/run_history_page.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:aura_client/l10n/generated/app_localizations_en.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _Token extends TokenManager {
  _Token(super.ref);
  @override
  Future<String?> getValidAccessToken() async => 'jwt';
}

class _SignedIn extends AuthNotifier {
  @override
  Future<UserProfile?> build() async =>
      UserProfile(id: 'u', email: 'u@example.com', displayName: 'u');

  @override
  Future<String?> getIdToken() async => null;
}

class _Gateway {
  final List<http.Request> requests = [];
  int status = 200;
  Map<String, dynamic> body = {
    'stopped': true,
    'scope': 'this_process',
    'turns_stopped': 2,
    'desktop_sessions_closed': 1,
  };

  http.Client client() => MockClient((request) async {
    requests.add(request);
    if (request.url.path.endsWith('/agents/runs/search')) {
      return http.Response(
        jsonEncode({
          'query': '',
          'total_matches': 0,
          'runs': [],
          'search_scope': {},
        }),
        200,
      );
    }
    return http.Response(jsonEncode(body), status);
  });
}

/// The page tests replace the repository: a widget test runs in a fake-async zone where the real HTTP
/// stack's platform-channel work (attestation headers) never completes. The HTTP shape is covered by the
/// repository tests above, which run the real client.
class _NoApi implements AuraApiClient {
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('the page tests do not use HTTP');
}

class _Repo extends RunHistoryRepository {
  _Repo(this.gateway) : super(_NoApi());

  final _Gateway gateway;
  int searches = 0;
  int stops = 0;

  @override
  Future<RunSearchResult> search({
    String query = '',
    String? status,
    int limit = 30,
  }) async {
    searches++;
    return const RunSearchResult(
      query: '',
      totalMatches: 0,
      runs: [],
      goalTextSearchable: false,
    );
  }

  @override
  Future<StopAllResult> stopAll() async {
    stops++;
    if (gateway.status != 200) throw StateError('boom');
    return StopAllResult.fromJson(gateway.body);
  }
}

ProviderContainer _container(_Gateway gateway) => ProviderContainer(
  overrides: [
    tokenManagerProvider.overrideWith((ref) => _Token(ref)),
    authProvider.overrideWith(_SignedIn.new),
    auraApiClientProvider.overrideWith(
      (ref) => AuraApiClient(ref, httpClient: gateway.client()),
    ),
  ],
);

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test(
    'the repository posts to stop-all without revoking devices and reads what was stopped',
    () async {
      final gateway = _Gateway();
      final container = _container(gateway);
      addTearDown(container.dispose);
      final result = await container
          .read(runHistoryRepositoryProvider)
          .stopAll();
      expect(result.turnsStopped, 2);
      expect(result.desktopSessionsClosed, 1);
      expect(result.scope, 'this_process');
      expect(result.stoppedAnything, isTrue);
      final sent = gateway.requests.single;
      expect(sent.method, 'POST');
      expect(sent.url.path, '/api/v3/agents/stop-all');
      expect(
        jsonDecode(sent.body),
        isNot(contains('revoke_devices')),
        reason: 'revoking devices is a separate act',
      );
    },
  );

  test(
    'nothing running reads as nothing stopped, and an odd body does not crash',
    () {
      expect(
        StopAllResult.fromJson({
          'turns_stopped': 0,
          'desktop_sessions_closed': 0,
        }).stoppedAnything,
        isFalse,
      );
      final odd = StopAllResult.fromJson({'turns_stopped': 'x'});
      expect(odd.turnsStopped, 0);
      expect(odd.scope, '');
    },
  );

  late _Repo repo;

  Future<void> pump(WidgetTester tester, _Gateway gateway) async {
    repo = _Repo(gateway);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [runHistoryRepositoryProvider.overrideWithValue(repo)],
        child: MaterialApp(
          theme: AuraThemeV2.dark,
          localizationsDelegates: const [
            AppLocalizations.delegate,
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
          ],
          supportedLocales: AppLocalizations.supportedLocales,
          home: const RunHistoryPage(),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('pressing stop asks first, and cancelling sends nothing', (
    tester,
  ) async {
    final gateway = _Gateway();
    await pump(tester, gateway);
    await tester.tap(find.byKey(const ValueKey('stop-all-button')));
    await tester.pumpAndSettle();
    expect(find.text(AppLocalizationsEn().agentStopAllTitle), findsWidgets);
    await tester.tap(find.text(AppLocalizationsEn().cancel));
    await tester.pumpAndSettle();
    expect(repo.stops, 0);
  });

  testWidgets(
    'confirming stops, says how many turns and sessions, and refreshes the list',
    (tester) async {
      final gateway = _Gateway();
      await pump(tester, gateway);
      await tester.tap(find.byKey(const ValueKey('stop-all-button')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('stop-all-confirm')));
      await tester.pumpAndSettle();
      expect(repo.stops, 1);
      expect(
        find.text(AppLocalizationsEn().agentStopAllDone(2, 1)),
        findsOneWidget,
      );
      expect(
        repo.searches,
        greaterThan(1),
        reason:
            'the run list is reloaded so the stopped runs show their new state',
      );
    },
  );

  testWidgets(
    'nothing running says so, and a failure is shown instead of swallowed',
    (tester) async {
      final gateway = _Gateway()
        ..body = {
          'stopped': true,
          'turns_stopped': 0,
          'desktop_sessions_closed': 0,
        };
      await pump(tester, gateway);
      await tester.tap(find.byKey(const ValueKey('stop-all-button')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('stop-all-confirm')));
      await tester.pumpAndSettle();
      expect(
        find.text(AppLocalizationsEn().agentStopAllNothing),
        findsOneWidget,
      );

      await tester.pump(
        const Duration(seconds: 6),
      ); // let the first snackbar leave
      await tester.pumpAndSettle();
      gateway
        ..status = 500
        ..body = {'error': 'internal_error', 'message': 'boom'};
      await tester.tap(find.byKey(const ValueKey('stop-all-button')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('stop-all-confirm')));
      await tester.pumpAndSettle();
      expect(find.byType(SnackBar), findsOneWidget);
      expect(find.text(AppLocalizationsEn().agentStopAllNothing), findsNothing);
    },
  );
}
