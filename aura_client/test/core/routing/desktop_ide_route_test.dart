// WP7: `/ide` off a Desktop IDE platform (macOS, mobile, web) used to render `SizedBox.shrink()`,
// a blank screen with no way back. It now says the IDE is not available here and offers Home.
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/routing/router.dart';
import 'package:aura_client/desktop/shell/desktop_ide_unavailable_page.dart';
import 'package:aura_client/desktop/shell/desktop_shell.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:aura_client/l10n/generated/app_localizations_en.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

final _l10n = AppLocalizationsEn();

void main() {
  tearDown(() => debugDefaultTargetPlatformOverride = null);

  Future<void> pumpIdeRoute(WidgetTester tester, String location) async {
    final router = GoRouter(
      initialLocation: location,
      routes: [
        GoRoute(path: '/today', builder: (context, state) => const Text('home page')),
        ShellRoute(
          builder: desktopIdeShellBuilder,
          routes: [
            GoRoute(path: '/ide', builder: (context, state) => const Text('welcome')),
            GoRoute(path: '/ide/workspace', builder: (context, state) => const Text('workspaces')),
          ],
        ),
      ],
    );
    await tester.pumpWidget(MaterialApp.router(
      theme: AuraThemeV2.dark,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      routerConfig: router,
    ));
    await tester.pumpAndSettle();
  }

  for (final platform in [TargetPlatform.macOS, TargetPlatform.android, TargetPlatform.iOS]) {
    for (final location in ['/ide', '/ide/workspace']) {
      testWidgets('$location on ${platform.name} explains, instead of a blank screen',
          (tester) async {
        debugDefaultTargetPlatformOverride = platform;
        await pumpIdeRoute(tester, location);

        expect(find.byType(DesktopIdeUnavailablePage), findsOneWidget);
        expect(find.text(_l10n.desktopIdeUnavailableTitle), findsOneWidget);
        expect(find.text('welcome'), findsNothing);
        expect(find.text('workspaces'), findsNothing);
        debugDefaultTargetPlatformOverride = null;
      });
    }
  }

  testWidgets('its button goes back to Home', (tester) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.macOS;
    await pumpIdeRoute(tester, '/ide');

    await tester.tap(find.text(_l10n.desktopIdeUnavailableGoHome));
    await tester.pumpAndSettle();

    expect(find.text('home page'), findsOneWidget);
    expect(find.byType(DesktopIdeUnavailablePage), findsNothing);
    debugDefaultTargetPlatformOverride = null;
  });

  for (final platform in [TargetPlatform.windows, TargetPlatform.linux]) {
    testWidgets('on ${platform.name} the shell is the IDE itself', (tester) async {
      debugDefaultTargetPlatformOverride = platform;
      late Widget built;
      await tester.pumpWidget(MaterialApp(
        home: Builder(builder: (context) {
          built = desktopIdeShellBuilder(context, _FakeState(), const Text('child'));
          return const SizedBox.shrink();
        }),
      ));
      expect(built, isA<DesktopShell>());
      debugDefaultTargetPlatformOverride = null;
    });
  }
}

class _FakeState extends Fake implements GoRouterState {}
