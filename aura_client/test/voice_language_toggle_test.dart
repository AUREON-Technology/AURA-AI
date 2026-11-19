// Phase V: the English / العربية toggle on the live-voice screen.

import 'package:aura_client/core/ai/voice/voice_language.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/voice_live/presentation/widgets/voice_language_toggle.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _app(Widget child, TextDirection direction) => MaterialApp(
      theme: AuraThemeV2.light,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      builder: (context, inner) => Directionality(textDirection: direction, child: inner!),
      home: Scaffold(
        body: Row(children: [const Icon(Icons.close), const Spacer(), child]),
      ),
    );

void main() {
  for (final direction in TextDirection.values) {
    testWidgets('shows both languages and reports a tap ($direction, 320 px)', (tester) async {
      tester.view.physicalSize = const Size(320, 640);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      VoiceLanguage? picked;
      await tester.pumpWidget(_app(
        VoiceLanguageToggle(value: VoiceLanguage.english, onChanged: (l) => picked = l),
        direction,
      ));

      expect(find.text('English'), findsOneWidget);
      expect(find.text('العربية'), findsOneWidget);
      expect(tester.takeException(), isNull, reason: 'no overflow at phone width');

      await tester.tap(find.text('العربية'));
      await tester.pump();
      expect(picked, VoiceLanguage.arabic);
    });
  }

  testWidgets('the Arabic label is laid out right-to-left inside an English screen', (tester) async {
    await tester.pumpWidget(_app(
      VoiceLanguageToggle(value: VoiceLanguage.arabic, onChanged: (_) {}),
      TextDirection.ltr,
    ));
    final arabic = tester.widget<Text>(find.text('العربية'));
    expect(arabic.textDirection, TextDirection.rtl);
  });
}
