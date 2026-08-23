import 'package:aura_client/core/design_system/design_system.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _wrap(Widget child) {
  return MaterialApp(
    theme: AuraThemeV2.light,
    home: Scaffold(body: child),
  );
}

void main() {
  testWidgets('AuraTextField exposes its label', (tester) async {
    await tester.pumpWidget(_wrap(const AuraTextField(label: 'Title')));
    expect(find.text('Title'), findsOneWidget);
  });

  testWidgets('AuraListTile is a 44px target and announces title', (tester) async {
    await tester.pumpWidget(
      _wrap(
        AuraListTile(
          icon: Icons.person_outline,
          title: 'Profile',
          subtitle: 'Account',
          onTap: () {},
        ),
      ),
    );
    expect(find.text('Profile'), findsOneWidget);
    final size = tester.getSize(find.byType(InkWell));
    expect(size.height, greaterThanOrEqualTo(44));
  });
}
