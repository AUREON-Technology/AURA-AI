import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/routing/router.dart';
import 'package:aura_client/core/sync/sync_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

void main() {
  testWidgets('shell exposes Today, Copilot, Life Hub, and You', (tester) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);

    final router = GoRouter(
      initialLocation: '/today',
      routes: [
        ShellRoute(
          builder: (context, state, child) => ShellLayout(child: child),
          routes: [
            GoRoute(path: '/today', builder: (_, _) => const Text('Today body')),
            GoRoute(path: '/chat', builder: (_, _) => const Text('Chat body')),
            GoRoute(path: '/life', builder: (_, _) => const Text('Life body')),
            GoRoute(path: '/you', builder: (_, _) => const Text('You body')),
          ],
        ),
      ],
    );

    await tester.pumpWidget(
      ProviderScope(
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
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Today'), findsWidgets);
    expect(find.text('Copilot'), findsOneWidget);
    expect(find.text('Life Hub'), findsOneWidget);
    expect(find.text('You'), findsOneWidget);
    expect(find.text('Finance'), findsNothing);
  });

  testWidgets('navigation bar indicator is themed and centered at 360px', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 640);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);

    final router = GoRouter(
      initialLocation: '/chat',
      routes: [
        ShellRoute(
          builder: (context, state, child) => ShellLayout(child: child),
          routes: [
            GoRoute(path: '/today', builder: (_, _) => const Text('Today body')),
            GoRoute(path: '/chat', builder: (_, _) => const Text('Chat body')),
            GoRoute(path: '/life', builder: (_, _) => const Text('Life body')),
            GoRoute(path: '/you', builder: (_, _) => const Text('You body')),
          ],
        ),
      ],
    );

    await tester.pumpWidget(
      ProviderScope(
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
      ),
    );
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
