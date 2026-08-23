import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/routing/router.dart';
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
}
