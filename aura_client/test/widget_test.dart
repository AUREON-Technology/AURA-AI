import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/main.dart';

void main() {
  testWidgets('Aura app boots smoke test', (WidgetTester tester) async {
    // Build our app and trigger a frame.
    await tester.pumpWidget(
      const ProviderScope(
        child: AuraApp(),
      ),
    );

    // Verify that the login page or splash load starts
    expect(find.byType(MaterialApp), findsOneWidget);
  });
}
