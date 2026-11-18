import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/routing/router.dart';
import 'package:aura_client/core/sync/sync_client.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

GoRouter _shellRouter(String initialLocation) => GoRouter(
      initialLocation: initialLocation,
      routes: [
        ShellRoute(
          builder: (context, state, child) => ShellLayout(child: child),
          routes: [
            GoRoute(path: '/chat', builder: (_, _) => const Text('Chat body')),
            GoRoute(
              path: '/engineer-history',
              builder: (_, _) => const Text('Engineer body'),
            ),
            GoRoute(path: '/you', builder: (_, _) => const Text('You body')),
          ],
        ),
      ],
    );

Widget _app(GoRouter router) => ProviderScope(
      // ShellLayout now carries SyncAlertBanner, which reads the parked
      // outbox count from the database. Left to the real provider that opens
      // sqlite and leaves a pending timer behind, which fails this test at
      // teardown; this test is about shell IA, so the count is stubbed —
      // same reason overflow_layout_test stubs it for ChatHeader.
      overrides: [
        parkedOutboxCountProvider.overrideWith((ref) => Stream.value(0)),
      ],
      child: MaterialApp.router(
        theme: AuraThemeV2.light,
        darkTheme: AuraThemeV2.dark,
        routerConfig: router,
      ),
    );

void main() {
  testWidgets('mobile shell exposes Copilot, Engineer and You, not Today or Life Hub',
      (tester) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);

    await tester.pumpWidget(_app(_shellRouter('/chat')));
    await tester.pumpAndSettle();

    expect(find.text('Copilot'), findsOneWidget);
    expect(find.text('Engineer'), findsOneWidget);
    expect(find.text('You'), findsOneWidget);
    expect(find.text('Today'), findsNothing);
    expect(find.text('Life Hub'), findsNothing);
    expect(find.text('Desktop IDE'), findsNothing);
  });

  testWidgets('Windows shell exposes only the Desktop IDE and You', (tester) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);

    // Foundation debug variables must be restored inside the test body.
    debugDefaultTargetPlatformOverride = TargetPlatform.windows;
    try {
      await tester.pumpWidget(_app(_shellRouter('/you')));
      await tester.pumpAndSettle();

      expect(find.text('Desktop IDE'), findsOneWidget);
      expect(find.text('You'), findsOneWidget);
      expect(find.text('Copilot'), findsNothing);
      expect(find.text('Engineer'), findsNothing);
      expect(find.text('Today'), findsNothing);
      expect(find.text('Life Hub'), findsNothing);
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('navigation bar indicator is themed and centered at 360px', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 640);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);

    await tester.pumpWidget(_app(_shellRouter('/chat')));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.byType(NavigationBar), findsOneWidget);

    final navTheme = NavigationBarTheme.of(
      tester.element(find.byType(NavigationBar)),
    );
    expect(navTheme.indicatorColor, isNotNull);
    expect(navTheme.indicatorShape, isA<StadiumBorder>());
    final selectedIcon = navTheme.iconTheme?.resolve({WidgetState.selected});
    expect(selectedIcon?.size, 24);

    final selectedIconFinder = find.byIcon(Icons.auto_awesome);
    expect(selectedIconFinder, findsOneWidget);
    final iconCenter = tester.getCenter(selectedIconFinder);
    final barBox = tester.getRect(find.byType(NavigationBar));
    expect(iconCenter.dx, greaterThan(barBox.left));
    expect(iconCenter.dx, lessThan(barBox.right));
  });
}
