import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/voice_live/presentation/widgets/aura_voice_orb.dart';

void main() {
  testWidgets('AuraVoiceOrb reduced-motion static branch', (tester) async {
    await tester.pumpWidget(
      MediaQuery(
        data: const MediaQueryData(disableAnimations: true),
        child: MaterialApp(
          theme: ThemeData(
            extensions: const [AuraPalette.light],
          ),
          home: const Scaffold(
            body: Center(
              child: AuraVoiceOrb(phase: 'listening', amplitude: 0.8),
            ),
          ),
        ),
      ),
    );
    await tester.pump();
    expect(find.byType(AuraVoiceOrb), findsOneWidget);
  });
}
