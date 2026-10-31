import 'dart:async';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/security/encryption_service.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/features/auth/presentation/login_page.dart';
import 'package:aura_client/features/chat/presentation/widgets/chat_composer.dart';
import 'package:aura_client/features/chat/presentation/widgets/empty_state_chat.dart';
import 'package:aura_client/features/settings/presentation/settings_page.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'source_scan.dart';

// RTL readiness (audit F-36 / R4.6). Arabic TTS ships (GAP-020) but there is no Arabic UI yet;
// this is the groundwork that has to hold before one is added:
//
//  1. a source guard: layout code under lib/ uses the directional APIs (EdgeInsetsDirectional,
//     AlignmentDirectional, PositionedDirectional, BorderRadiusDirectional), so a right-to-left
//     locale mirrors the layout instead of keeping a left-to-right one with RTL text in it;
//  2. key screens pumped under TextDirection.rtl at phone width, with the real localization
//     delegates, render without an overflow or any other framework exception.
//
// What this cannot settle headless: how a screen reader announces the mirrored screens, and
// glyph shaping/font fallback for Arabic script on each platform.

/// `Dialog.insetPadding` is typed `EdgeInsets`, so it cannot take a directional value; the two
/// call sites that set it use equal left and right insets, which are direction-neutral.
final _insetPaddingArg = RegExp(r'insetPadding\s*:\s*$');

final _physicalCalls = <RegExp, RegExp>{
  // ctor                                   physical named arg at the call's own level
  RegExp(r'(?<![\w$.])EdgeInsets\.only\('): RegExp(r'(left|right)\s*:'),
  RegExp(r'(?<![\w$.])Positioned\('): RegExp(r'(left|right)\s*:'),
  RegExp(r'(?<![\w$.])BorderRadius\.only\('): RegExp(r'(topLeft|topRight|bottomLeft|bottomRight)\s*:'),
  RegExp(r'(?<![\w$.])Border\('): RegExp(r'(left|right)\s*:'),
};

final _physicalAlignment = RegExp(r'(?<![\w$.])Alignment\.(centerLeft|centerRight)\b');

List<String> _physicalLayoutUses() {
  final found = <String>[];
  for (final file in dartSources('lib')) {
    final masked = maskDart(file.readAsStringSync());
    for (final entry in _physicalCalls.entries) {
      for (final m in entry.key.allMatches(masked)) {
        final before = masked.substring(0, m.start).trimRight();
        if (_insetPaddingArg.hasMatch('$before ')) continue;
        final args = masked.substring(m.end, closingParen(masked, m.end) - 1);
        if (hasTopLevelNamedArg(args, entry.value)) {
          found.add('${file.path}:${lineOf(masked, m.start)} ${m.group(0)}');
        }
      }
    }
    for (final m in _physicalAlignment.allMatches(masked)) {
      found.add('${file.path}:${lineOf(masked, m.start)} ${m.group(0)}');
    }
  }
  return found;
}

/// The real app's localization wiring, forced right-to-left at a phone-sized surface.
Widget _rtlApp(Widget home) => MaterialApp(
      theme: AuraThemeV2.light,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      builder: (context, child) => Directionality(
        textDirection: TextDirection.rtl,
        child: child!,
      ),
      home: home,
    );

Future<void> _setPhoneSize(WidgetTester tester) async {
  tester.view.physicalSize = const Size(360, 740);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
}

class _SignedOut extends AuthNotifier {
  @override
  FutureOr<UserProfile?> build() => null;
}

void main() {
  test('layout code under lib/ uses directional, not physical, left/right', () {
    expect(
      _physicalLayoutUses(),
      isEmpty,
      reason: 'Use EdgeInsetsDirectional / AlignmentDirectional / PositionedDirectional / '
          'BorderRadiusDirectional (start/end) so an RTL locale mirrors the layout. Purely '
          'decorative geometry (a gradient\'s begin/end, a shadow offset) is not checked.',
    );
  });

  group('under TextDirection.rtl', () {
    // Real async work: it would never complete inside a testWidgets body's fake-async zone.
    setUpAll(EncryptionService.init);

    testWidgets('the chat composer lays out mirrored, without overflow', (tester) async {
      await _setPhoneSize(tester);
      final db = AppDatabase.forTesting(NativeDatabase.memory());
      final container = ProviderContainer(
        overrides: [
          databaseProvider.overrideWith((ref) {
            ref.onDispose(db.close);
            return db;
          }),
        ],
      );
      addTearDown(container.dispose);

      await tester.pumpWidget(
        UncontrolledProviderScope(
          container: container,
          child: _rtlApp(
            Scaffold(
              body: Align(
                alignment: AlignmentDirectional.bottomCenter,
                child: ChatComposer(
                  controller: TextEditingController(),
                  isMobile: true,
                  onSend: () {},
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pump();

      expect(tester.takeException(), isNull);
      final send = find.byTooltip('Send');
      expect(send, findsOneWidget);
      // The Row mirrors: Send, the last child, now sits left of the text field.
      final sendX = tester.getCenter(send).dx;
      final fieldX = tester.getCenter(find.byType(TextField)).dx;
      expect(sendX, lessThan(fieldX));
      expect(
        Directionality.of(tester.element(find.byType(TextField))),
        TextDirection.rtl,
      );
    });

    testWidgets('the empty chat state renders without overflow', (tester) async {
      await _setPhoneSize(tester);
      await tester.pumpWidget(
        _rtlApp(Scaffold(body: EmptyStateChat(onSuggestionSelected: (_) {}))),
      );
      await tester.pump();

      expect(tester.takeException(), isNull);
      expect(find.text('Ask Copilot Anything'), findsOneWidget);
    });

    testWidgets('the You screen renders without overflow', (tester) async {
      await _setPhoneSize(tester);
      await tester.pumpWidget(ProviderScope(child: _rtlApp(const SettingsPage())));
      await tester.pump();

      expect(tester.takeException(), isNull);
      expect(find.text('Profile'), findsOneWidget);
      // List tiles mirror: the leading icon is now on the right of its title.
      final iconX = tester.getCenter(find.byIcon(Icons.person_outline)).dx;
      final titleX = tester.getCenter(find.text('Profile')).dx;
      expect(iconX, greaterThan(titleX));
    });

    testWidgets('the sign-in page renders without overflow', (tester) async {
      await _setPhoneSize(tester);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [authProvider.overrideWith(_SignedOut.new)],
          child: _rtlApp(const LoginPage()),
        ),
      );
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));

      expect(tester.takeException(), isNull);
      expect(find.text('Welcome back'), findsOneWidget);
    });
  });
}
