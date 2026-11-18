// The Engineer, Linux IDE and remote-control strings (WP1, WP2, WP3, WP7, WP8) in Egyptian
// Arabic, on top of the general parity checks in arabic_locale_test.dart: each one is present and
// translated, the consent notice is marked as awaiting the owner's review of its Arabic (O-03),
// and a screen built from them renders right to left.
import 'dart:convert';
import 'dart:io';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/shell/desktop_ide_unavailable_page.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, dynamic> _arb(String name) =>
    jsonDecode(File('lib/l10n/$name').readAsStringSync()) as Map<String, dynamic>;

const _consentNotice = [
  'engineerConsentTitle',
  'engineerConsentBodyWhat',
  'engineerConsentBodyIncludes',
  'engineerConsentBodyChoice',
];

const _ourKeys = [
  ..._consentNotice,
  'engineerConsentAgree', 'engineerConsentNotNow', 'engineerConsentSaveFailed',
  'engineerConsentUpdateTitle', 'engineerConsentUpdateBody', 'engineerConsentUpdateOk',
  'engineerConsentNeededNotice', 'engineerLimitReached', 'engineerLimitReachedNoTime',
  'engineerConsentStatusTitle', 'engineerConsentStatusOn', 'engineerConsentStatusOnNoDate',
  'engineerConsentStatusOff', 'engineerConsentStatusOnSubtitle', 'engineerConsentStatusOffSubtitle',
  'engineerConsentStatusLoading', 'engineerConsentStatusError', 'engineerConsentWithdraw',
  'engineerConsentWithdrawTitle', 'engineerConsentWithdrawBody', 'engineerConsentWithdrawn',
  'engineerConsentWithdrawFailed',
  'onboardingPrivacyTitle', 'onboardingPrivacyBody', 'settingsAboutBody',
  'engineeringRootTrusted', 'engineeringRootUntrusted', 'chatConfirmRunCommand',
  'desktopIdeUnavailableTitle', 'desktopIdeUnavailableBody', 'desktopIdeUnavailableGoHome',
  'explorerRevealUnsupported',
  'hostdRemoteControlTitle', 'hostdRemoteControlExplanation', 'hostdRemoteControlConfirmTitle',
  'hostdRemoteControlConfirmBody', 'hostdRemoteControlConfirmAllow', 'hostdRemoteControlFailed',
  'engineerDesktopNotConnected', 'engineerDesktopRemoteOff', 'engineerDesktopNoFolders',
  'engineerDesktopFolderLabel', 'engineerDesktopCheckFailed', 'engineerDesktopRefresh',
  'hostdCodeGitPushNeedsApproval', 'hostdCodePathOutsideTrustedFolder',
  'hostdCodeRemoteCommandNeedsConfirmation', 'hostdCodeProtectedGitDirFile',
  'hostdCodeRemoteControlDisabled', 'hostdCodeRemoteConfirmationRequired', 'hostdCodeInvalidTaskKey',
  'hostdCodeIsolatedTaskUnresolved', 'hostdCodeInvalidOrigin', 'hostdCodeDiagnosticsUntrusted',
  'hostdCodeInvalidRequestBody',
];

final _arabicLetter = RegExp('[ء-ي]');

void main() {
  final en = _arb('app_en.arb');
  final ar = _arb('app_ar.arb');

  test('every Engineer, IDE and remote-control string is in both files and translated', () {
    for (final key in _ourKeys) {
      expect(en[key], isA<String>(), reason: '$key missing from app_en.arb');
      expect(ar[key], isA<String>(), reason: '$key missing from app_ar.arb');
      expect(ar[key], isNot(en[key]), reason: '$key is still English in app_ar.arb');
      expect(_arabicLetter.hasMatch(ar[key] as String), isTrue, reason: '$key has no Arabic');
    }
  });

  test('the consent notice says its Arabic awaits the owner (O-03)', () {
    for (final key in _consentNotice) {
      final description = (en['@$key'] as Map)['description'] as String;
      expect(description, contains('O-03'), reason: key);
      expect(description, contains('2026-11-17'), reason: key);
    }
  });

  test('the notice names the same company and model in Arabic as in English', () {
    for (final key in ['engineerConsentTitle', 'engineerConsentBodyWhat']) {
      for (final name in ['Claude', 'Anthropic']) {
        expect(ar[key], contains(name), reason: key);
      }
    }
  });

  test('our English wording wins where both sides changed a string', () {
    expect(en['chatConfirmRunCommand'], 'Run a command');
    expect(en['engineeringRootTrusted'], contains('anything your account can'));
    expect(en['engineeringRootTrusted'], isNot(contains('confined')));
    expect(en['engineeringRootUntrusted'], contains('anything else asks you first'));
    expect(ar['engineeringRootTrusted'], isNot(contains('جوّه الفولدر ده بس')));
  });

  testWidgets('the desktop-IDE-unavailable page renders in Arabic, right to left', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: AuraThemeV2.dark,
      locale: const Locale('ar'),
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      supportedLocales: AppLocalizations.supportedLocales,
      home: const DesktopIdeUnavailablePage(),
    ));
    await tester.pumpAndSettle();

    final ar = lookupAppLocalizations(const Locale('ar'));
    expect(find.text(ar.desktopIdeUnavailableTitle), findsOneWidget);
    expect(find.text(ar.desktopIdeUnavailableGoHome), findsOneWidget);
    expect(Directionality.of(tester.element(find.byType(DesktopIdeUnavailablePage))), TextDirection.rtl);
    expect(tester.takeException(), isNull);
  });
}
