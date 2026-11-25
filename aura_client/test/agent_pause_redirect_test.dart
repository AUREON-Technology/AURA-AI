// Pause / resume / redirect a running agent (ADR-059). The repository runs the real client against a
// MockClient that answers like the gateway; the page test drives the real buttons and dialog.

import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/features/engineering/data/run_history_repository.dart';
import 'package:aura_client/features/engineering/presentation/run_history_page.dart';
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

class _NoApi implements AuraApiClient {
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('the page tests do not use HTTP');
}

class _Repo extends RunHistoryRepository {
  _Repo(this.reached) : super(_NoApi());

  final int reached;
  final List<String> calls = [];

  @override
  Future<RunSearchResult> search({String query = '', String? status, int limit = 30}) async =>
      const RunSearchResult(query: '', totalMatches: 0, runs: [], goalTextSearchable: false);

  @override
  Future<int> pause() async {
    calls.add('pause');
    return reached;
  }

  @override
  Future<int> resume() async {
    calls.add('resume');
    return reached;
  }

  @override
  Future<int> redirect(String instruction) async {
    calls.add('redirect:$instruction');
    return reached;
  }
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('the repository posts pause, resume and redirect and reads how many turns were reached', () async {
    final requests = <http.Request>[];
    final client = MockClient((request) async {
      requests.add(request);
      final path = request.url.path;
      final key = path.endsWith('/pause')
          ? 'turns_paused'
          : path.endsWith('/resume')
              ? 'turns_resumed'
              : 'turns_redirected';
      return http.Response(jsonEncode({key: 1, 'scope': 'this_process'}), 200);
    });
    final container = ProviderContainer(overrides: [
      tokenManagerProvider.overrideWith((ref) => _Token(ref)),
      authProvider.overrideWith(_SignedIn.new),
      auraApiClientProvider.overrideWith((ref) => AuraApiClient(ref, httpClient: client)),
    ]);
    addTearDown(container.dispose);
    final repo = container.read(runHistoryRepositoryProvider);
    expect(await repo.pause(), 1);
    expect(await repo.resume(), 1);
    expect(await repo.redirect('use staging'), 1);
    expect(requests.map((r) => r.url.path), [
      '/api/v3/agents/pause',
      '/api/v3/agents/resume',
      '/api/v3/agents/redirect',
    ]);
    expect(jsonDecode(requests.last.body), {'instruction': 'use staging'});
  });

  test('a pause that only reached other workers still counts as reaching something', () async {
    final client = MockClient((request) async => http.Response(
        jsonEncode({'paused': true, 'turns_paused': 0, 'scope': 'all_workers'}), 200));
    final container = ProviderContainer(overrides: [
      tokenManagerProvider.overrideWith((ref) => _Token(ref)),
      authProvider.overrideWith(_SignedIn.new),
      auraApiClientProvider.overrideWith((ref) => AuraApiClient(ref, httpClient: client)),
    ]);
    addTearDown(container.dispose);
    expect(await container.read(runHistoryRepositoryProvider).pause(), 1);
  });

  Future<_Repo> pump(WidgetTester tester, int reached) async {
    final repo = _Repo(reached);
    await tester.pumpWidget(ProviderScope(
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
    ));
    await tester.pumpAndSettle();
    return repo;
  }

  testWidgets('pause and resume say what happened, and say so when nothing is running', (tester) async {
    final repo = await pump(tester, 1);
    await tester.tap(find.byKey(const ValueKey('pause-button')));
    await tester.pump();
    expect(find.text(AppLocalizationsEn().agentPauseDone), findsOneWidget);
    ScaffoldMessenger.of(tester.element(find.byType(Scaffold).first)).clearSnackBars();
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('resume-button')));
    await tester.pump();
    expect(find.text(AppLocalizationsEn().agentResumeDone), findsOneWidget);
    expect(repo.calls, ['pause', 'resume']);
  });

  testWidgets('nothing running is reported, not shown as success', (tester) async {
    await pump(tester, 0);
    await tester.tap(find.byKey(const ValueKey('pause-button')));
    await tester.pump();
    expect(find.text(AppLocalizationsEn().agentControlNothing), findsOneWidget);
  });

  testWidgets('redirect sends the typed instruction, and an empty one sends nothing', (tester) async {
    final repo = await pump(tester, 1);
    await tester.tap(find.byKey(const ValueKey('redirect-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('redirect-send')));
    await tester.pumpAndSettle();
    expect(repo.calls, isEmpty);

    await tester.tap(find.byKey(const ValueKey('redirect-button')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('redirect-field')), '  leave the migrations alone ');
    await tester.tap(find.byKey(const ValueKey('redirect-send')));
    await tester.pumpAndSettle();
    expect(repo.calls, ['redirect:leave the migrations alone']);
    expect(find.text(AppLocalizationsEn().agentRedirectDone), findsOneWidget);
  });
}
