import 'package:aura_client/core/ai/engineer/engineer_workspaces.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/routing/router.dart';
import 'package:aura_client/core/sync/sync_client.dart';
import 'package:aura_client/features/engineering/data/engineer_run_repository.dart';
import 'package:aura_client/features/engineering/presentation/engineer_history_page.dart';
import 'package:aura_client/features/settings/presentation/settings_page.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart' show Override;
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

/// The web build's wide layout (docs/plans/client-web-app_plan.md): the You menu stays beside the
/// open page, and Engineer's runs list beside the open run. Below the split width, and off the
/// web, both are the mobile screens.

final AppLocalizations _en = lookupAppLocalizations(const Locale('en'));
final AppLocalizations _ar = lookupAppLocalizations(const Locale('ar'));

Future<void> _size(WidgetTester tester, double w, [double h = 900]) async {
  tester.view.physicalSize = Size(w, h);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

Widget _host(Widget home, {Locale locale = const Locale('en'), List<Override> overrides = const []}) =>
    ProviderScope(
      overrides: [
        parkedOutboxCountProvider.overrideWith((ref) => Stream.value(0)),
        ...overrides,
      ],
      child: MaterialApp(
        theme: AuraThemeV2.light,
        locale: locale,
        localizationsDelegates: const [
          AppLocalizations.delegate,
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        home: home,
      ),
    );

GoRouter _shell({required bool isWeb, String start = '/you'}) => GoRouter(
      initialLocation: start,
      routes: [
        ShellRoute(
          builder: (context, state, child) => ShellLayout(isWeb: isWeb, child: child),
          routes: [
            GoRoute(path: '/chat', builder: (_, _) => const Text('Chat body')),
            GoRoute(path: '/you', builder: (_, _) => const SettingsPage()),
            GoRoute(path: '/profile', builder: (_, _) => const Text('Profile body')),
            GoRoute(path: '/appearance', builder: (_, _) => const Text('Appearance body')),
            GoRoute(path: '/github-connect', builder: (_, _) => const Text('GitHub body')),
          ],
        ),
      ],
    );

Widget _shellApp(GoRouter router, {Locale locale = const Locale('en')}) => ProviderScope(
      overrides: [parkedOutboxCountProvider.overrideWith((ref) => Stream.value(0))],
      child: MaterialApp.router(
        theme: AuraThemeV2.light,
        locale: locale,
        localizationsDelegates: const [
          AppLocalizations.delegate,
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
        supportedLocales: AppLocalizations.supportedLocales,
        routerConfig: router,
      ),
    );

EngineerRun _run(String id, String branch) => EngineerRun(
      id: id,
      turnId: 't$id',
      correlationId: 'c$id',
      persona: 'engineer',
      tool: 'github_create_branch',
      callId: 'k$id',
      status: 'succeeded',
      branch: branch,
      createdAt: DateTime(2026, 11, 20),
      updatedAt: DateTime(2026, 11, 20),
    );

final _runOverrides = <Override>[
  engineerRunsProvider.overrideWith((ref) async => [_run('1', 'feature/one'), _run('2', 'feature/two')]),
  engineerWorkspacesProvider.overrideWith((ref) async => EngineerWorkspaceList.empty),
];

void main() {
  group('masterDetailSplits', () {
    test('only the web, and only with room for two panes', () {
      expect(masterDetailSplits(1000, isWeb: true), isTrue);
      expect(masterDetailSplits(840, isWeb: true), isTrue);
      expect(masterDetailSplits(839, isWeb: true), isFalse);
      expect(masterDetailSplits(2000, isWeb: false), isFalse);
    });
  });

  group('You on a wide web window', () {
    testWidgets('shows the menu and a prompt, then the chosen page beside the menu', (tester) async {
      await _size(tester, 1280);
      final router = _shell(isWeb: true);
      await tester.pumpWidget(_shellApp(router));
      await tester.pumpAndSettle();

      expect(find.text(_en.settingsProfileTitle), findsOneWidget);
      expect(find.text(_en.webYouPaneTitle), findsOneWidget);

      await tester.tap(find.text(_en.settingsProfileTitle));
      await tester.pumpAndSettle();

      // The menu is still there, the page opened beside it, and nothing was stacked on top.
      expect(find.text(_en.settingsProfileTitle), findsOneWidget);
      expect(find.text('Profile body'), findsOneWidget);
      expect(find.text(_en.webYouPaneTitle), findsNothing);
      expect(router.canPop(), isFalse);

      await tester.tap(find.text(_en.appearanceTitle));
      await tester.pumpAndSettle();
      expect(find.text('Appearance body'), findsOneWidget);
      expect(find.text('Profile body'), findsNothing);
      expect(find.text(_en.settingsProfileTitle), findsOneWidget);
    });

    testWidgets('the open row is marked selected', (tester) async {
      await _size(tester, 1280);
      await tester.pumpWidget(_shellApp(_shell(isWeb: true, start: '/profile')));
      await tester.pumpAndSettle();

      final selected = find.byWidgetPredicate(
        (w) => w is Semantics && w.properties.selected == true && (w.properties.label ?? '').startsWith(_en.settingsProfileTitle),
      );
      expect(selected, findsOneWidget);
    });

    testWidgets('Arabic mirrors it: the menu is on the right of the page', (tester) async {
      await _size(tester, 1280);
      await tester.pumpWidget(_shellApp(_shell(isWeb: true, start: '/profile'), locale: const Locale('ar')));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      final menuX = tester.getCenter(find.text(_ar.settingsProfileTitle)).dx;
      final pageX = tester.getCenter(find.text('Profile body')).dx;
      expect(menuX, greaterThan(pageX));
    });

    testWidgets('Engineer\'s other pages stay one column', (tester) async {
      await _size(tester, 1280);
      await tester.pumpWidget(_shellApp(_shell(isWeb: true, start: '/github-connect')));
      await tester.pumpAndSettle();
      expect(find.text('GitHub body'), findsOneWidget);
      expect(find.text(_en.settingsProfileTitle), findsNothing);
    });

    testWidgets('Copilot is left to lay itself out', (tester) async {
      await _size(tester, 1280);
      await tester.pumpWidget(_shellApp(_shell(isWeb: true, start: '/chat')));
      await tester.pumpAndSettle();
      expect(find.text('Chat body'), findsOneWidget);
      expect(find.text(_en.settingsProfileTitle), findsNothing);
    });
  });

  group('You where it does not split', () {
    testWidgets('a narrower web window keeps the single column', (tester) async {
      await _size(tester, 900); // 900 minus the rail leaves under 840
      await tester.pumpWidget(_shellApp(_shell(isWeb: true, start: '/profile')));
      await tester.pumpAndSettle();
      expect(find.text('Profile body'), findsOneWidget);
      expect(find.text(_en.settingsProfileTitle), findsNothing);
    });

    testWidgets('a phone-width window is the mobile shell', (tester) async {
      await _size(tester, 390, 800);
      await tester.pumpWidget(_shellApp(_shell(isWeb: true, start: '/profile')));
      await tester.pumpAndSettle();
      expect(find.byType(NavigationBar), findsOneWidget);
      expect(find.text('Profile body'), findsOneWidget);
      expect(find.text(_en.settingsProfileTitle), findsNothing);
    });

    testWidgets('a wide native window never splits', (tester) async {
      await _size(tester, 1280);
      await tester.pumpWidget(_shellApp(_shell(isWeb: false, start: '/profile')));
      await tester.pumpAndSettle();
      expect(find.text('Profile body'), findsOneWidget);
      expect(find.text(_en.settingsProfileTitle), findsNothing);
    });

    testWidgets('You on its own pushes pages as the mobile app does', (tester) async {
      await _size(tester, 390, 800);
      final router = _shell(isWeb: false);
      // /profile is a plain Text here, so the push is visible as a stack entry.
      await tester.pumpWidget(_shellApp(router));
      await tester.pumpAndSettle();
      await tester.tap(find.text(_en.settingsProfileTitle));
      await tester.pumpAndSettle();
      expect(find.text('Profile body'), findsOneWidget);
      expect(router.canPop(), isTrue);
    });
  });

  group('Engineer runs', () {
    testWidgets('a wide web window opens a run beside the list', (tester) async {
      await _size(tester, 1280);
      await tester.pumpWidget(_host(const EngineerHistoryPage(isWeb: true), overrides: _runOverrides));
      await tester.pumpAndSettle();

      expect(find.text(_en.webEngineerPaneTitle), findsOneWidget);
      await tester.tap(find.text('feature/two'));
      await tester.pumpAndSettle();

      expect(find.text(_en.webEngineerPaneTitle), findsNothing);
      expect(find.text(_en.engineeringRunBranch('feature/two')), findsOneWidget);
      // The list is still there, so another run is one tap away, and no sheet was opened.
      expect(find.text('feature/one'), findsOneWidget);
      expect(find.byType(BottomSheet), findsNothing);

      await tester.tap(find.text('feature/one'));
      await tester.pumpAndSettle();
      expect(find.text(_en.engineeringRunBranch('feature/one')), findsOneWidget);
      expect(find.text(_en.engineeringRunBranch('feature/two')), findsNothing);
    });

    testWidgets('Arabic puts the run list on the right', (tester) async {
      await _size(tester, 1280);
      await tester.pumpWidget(
        _host(const EngineerHistoryPage(isWeb: true), locale: const Locale('ar'), overrides: _runOverrides),
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      final listX = tester.getCenter(find.text('feature/one')).dx;
      final paneX = tester.getCenter(find.text(_ar.webEngineerPaneTitle)).dx;
      expect(listX, greaterThan(paneX));
    });

    testWidgets('a phone opens a run in a bottom sheet, as before', (tester) async {
      await _size(tester, 390, 800);
      await tester.pumpWidget(_host(const EngineerHistoryPage(isWeb: true), overrides: _runOverrides));
      await tester.pumpAndSettle();

      expect(find.text(_en.webEngineerPaneTitle), findsNothing);
      await tester.tap(find.text('feature/one'));
      await tester.pumpAndSettle();
      expect(find.byType(BottomSheet), findsOneWidget);
      expect(find.text(_en.engineeringRunBranch('feature/one')), findsOneWidget);
    });

    testWidgets('off the web a wide window still opens the sheet', (tester) async {
      await _size(tester, 1280);
      await tester.pumpWidget(_host(const EngineerHistoryPage(isWeb: false), overrides: _runOverrides));
      await tester.pumpAndSettle();
      await tester.tap(find.text('feature/one'));
      await tester.pumpAndSettle();
      expect(find.byType(BottomSheet), findsOneWidget);
    });
  });
}
