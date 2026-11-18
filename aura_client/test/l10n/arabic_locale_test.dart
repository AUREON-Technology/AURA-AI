import 'dart:convert';
import 'dart:io';

import 'package:aura_client/core/locale/app_language.dart';
import 'package:aura_client/core/platform/client_language.dart';
import 'package:aura_client/core/theme/theme_mode_controller.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/settings/presentation/appearance_page.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:intl/intl.dart' show DateFormat, NumberFormat;

// Arabic (Egyptian) in the app (docs/plans/arabic-language_plan.md, stage 2; owner decisions
// 2, 4, 7, 8): every English string has an Arabic one, the user can pick the language in
// Settings -> Appearance, the choice persists and reaches the backend, and Arabic is formatted
// with Western digits.

Map<String, dynamic> _arb(String name) =>
    jsonDecode(File('lib/l10n/$name').readAsStringSync()) as Map<String, dynamic>;

/// Messages that are the same in both languages on purpose: brand names, and strings made only
/// of placeholders, symbols or Latin technical vocabulary Egyptians use as-is (decision 6).
const _identicalOnPurpose = <String>{
  'appName', // AURA
  'navCopilot', // product name, as in "Ask Copilot"
  'planStepWithStatus', // placeholders only
  // Git and debugger vocabulary Egyptian developers use as-is (decision 6).
  'gitCommit', 'gitBranch', 'gitStage', 'gitUnstage', 'gitMerge', 'gitDetachedHead',
  'controlCenterMerge', 'controlCenterSquashMerge', 'coreEngineerWorkspaceBranch',
  'debugStepOver', 'debugStepInto', 'debugStepOut',
  // Server environment names, shown as configured.
  'coreBackendEnvironmentProduction', 'coreBackendEnvironmentDevelopment',
  // Paid plan names are product names, kept in Latin script as elsewhere ("اشترك في Pro").
  'planNamePro', 'planNameTeam',
};

void main() {
  group('translation files', () {
    final en = _arb('app_en.arb');
    final ar = _arb('app_ar.arb');
    final messageKeys = en.keys.where((k) => !k.startsWith('@')).toList();

    test('every English message has an Arabic one', () {
      final missing = messageKeys.where((k) => !ar.containsKey(k)).toList();
      expect(missing, isEmpty, reason: 'add these keys to lib/l10n/app_ar.arb');
      final extra = ar.keys.where((k) => !k.startsWith('@') && !en.containsKey(k)).toList();
      expect(extra, isEmpty, reason: 'keys in app_ar.arb that app_en.arb does not define');
    });

    test('Arabic keeps every placeholder the English message declares', () {
      final broken = <String>[];
      for (final key in messageKeys) {
        final meta = en['@$key'];
        final placeholders = meta is Map ? (meta['placeholders'] as Map?)?.keys ?? const [] : const [];
        final arabic = ar[key];
        if (arabic is! String) continue;
        for (final name in placeholders) {
          if (!RegExp('\\{$name[,}]').hasMatch(arabic)) broken.add('$key: {$name}');
        }
      }
      expect(broken, isEmpty);
    });

    test('Arabic messages are translated, not copied', () {
      final copied = messageKeys
          .where((k) => ar[k] is String && (ar[k] as String).trim().isEmpty ||
              (ar[k] == en[k] && !_identicalOnPurpose.contains(k)))
          .toList();
      expect(copied, isEmpty, reason: 'empty or still English in app_ar.arb');
    });
  });

  group('AppLanguage', () {
    setUp(() => FlutterSecureStorage.setMockInitialValues({}));
    tearDown(() => ClientLanguage.override = null);

    test('a stored choice is restored and drives the request header', () async {
      FlutterSecureStorage.setMockInitialValues({'aura_app_language': 'ar'});
      final container = ProviderContainer();
      addTearDown(container.dispose);

      expect(container.read(appLanguageProvider), AppLanguage.system);
      await Future<void>.delayed(Duration.zero);
      expect(container.read(appLanguageProvider), AppLanguage.arabic);
      expect(ClientLanguage.current(), 'ar');
    });

    test('a new choice is saved, and System falls back to the device', () async {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      container.read(appLanguageProvider);

      await container.read(appLanguageProvider.notifier).setLanguage(AppLanguage.english);
      expect(await const FlutterSecureStorage().read(key: 'aura_app_language'), 'en');
      expect(ClientLanguage.current(), 'en');

      await container.read(appLanguageProvider.notifier).setLanguage(AppLanguage.system);
      expect(await const FlutterSecureStorage().read(key: 'aura_app_language'), 'system');
      expect(container.read(appLanguageProvider).locale, isNull);
    });
  });

  testWidgets('picking العربية in Appearance switches the app to Arabic, right to left',
      (tester) async {
    FlutterSecureStorage.setMockInitialValues({});
    await tester.pumpWidget(
      ProviderScope(
        child: Consumer(
          builder: (context, ref, _) => MaterialApp(
            theme: AuraThemeV2.light,
            themeMode: ref.watch(themeModeProvider),
            locale: ref.watch(appLanguageProvider).locale ?? const Locale('en'),
            localizationsDelegates: const [
              AppLocalizations.delegate,
              GlobalMaterialLocalizations.delegate,
              GlobalWidgetsLocalizations.delegate,
              GlobalCupertinoLocalizations.delegate,
            ],
            supportedLocales: AppLocalizations.supportedLocales,
            home: const AppearancePage(),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Language'), findsOneWidget);

    await tester.scrollUntilVisible(find.byKey(const ValueKey('language-arabic')), 100);
    await tester.tap(find.byKey(const ValueKey('language-arabic')));
    await tester.pumpAndSettle();

    expect(find.text('اللغة'), findsOneWidget);
    final context = tester.element(find.byType(AppearancePage));
    expect(Directionality.of(context), TextDirection.rtl);
    // Each language keeps its own name, so it can be found whatever the current language is.
    expect(find.text('English'), findsOneWidget);
    expect(find.text('العربية'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  test('Arabic dates and numbers use Western digits (decision 7)', () async {
    await initializeDateFormatting('ar');
    final when = DateTime(2026, 11, 17, 13, 30);
    final text = '${DateFormat.yMMMd('ar').format(when)} ${DateFormat.jm('ar').format(when)} '
        '${NumberFormat.decimalPattern('ar').format(1234.5)}';
    expect(text, contains('2026'));
    expect(text, contains('1:30'));
    expect(text, contains('1,234.5'));
    expect(RegExp('[٠-٩۰-۹]').hasMatch(text), isFalse, reason: text);
  });
}
