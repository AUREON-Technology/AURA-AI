import 'package:aura_client/features/chat/presentation/chat_route_extra.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

void main() {
  group('chatInitialQueryFromExtra', () {
    test('parses a non-empty string extra', () {
      expect(chatInitialQueryFromExtra('  buy milk  '), 'buy milk');
    });

    test('rejects empty, non-string, and null extra', () {
      expect(chatInitialQueryFromExtra(null), isNull);
      expect(chatInitialQueryFromExtra(''), isNull);
      expect(chatInitialQueryFromExtra('   '), isNull);
      expect(chatInitialQueryFromExtra(123), isNull);
    });
  });

  testWidgets('GoRouter extra string is mapped into ChatPage.initialQuery', (tester) async {
    String? seen;
    final router = GoRouter(
      initialLocation: '/home',
      routes: [
        GoRoute(
          path: '/home',
          builder: (context, state) => const SizedBox.shrink(),
        ),
        GoRoute(
          path: '/chat',
          builder: (context, state) {
            seen = chatInitialQueryFromExtra(state.extra);
            return Text(seen ?? 'none');
          },
        ),
      ],
    );
    addTearDown(router.dispose);

    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/chat', extra: 'from capture bar');
    await tester.pumpAndSettle();

    expect(seen, 'from capture bar');
    expect(find.text('from capture bar'), findsOneWidget);
  });
}
