import 'package:aura_client/core/config/app_links.dart';
import 'package:aura_client/features/engineering/presentation/web_daemon_notice.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _host(Locale locale) => MaterialApp(
      locale: locale,
      localizationsDelegates: const [
        AppLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      supportedLocales: AppLocalizations.supportedLocales,
      home: const WebDaemonNotice(),
    );

void main() {
  testWidgets('says where to pair a computer, in English', (tester) async {
    await tester.pumpWidget(_host(const Locale('en')));
    await tester.pumpAndSettle();
    expect(find.text('Pair a computer in the desktop app'), findsOneWidget);
    expect(find.textContaining("can't run the AURA desktop daemon"), findsOneWidget);
  });

  testWidgets('says it in Arabic, right to left', (tester) async {
    await tester.pumpWidget(_host(const Locale('ar')));
    await tester.pumpAndSettle();
    expect(find.text('اربط كمبيوترك من تطبيق سطح المكتب'), findsOneWidget);
    final direction = Directionality.of(tester.element(find.byType(WebDaemonNotice)));
    expect(direction, TextDirection.rtl);
  });

  testWidgets('with no known web address it offers only the paired-devices list', (tester) async {
    await tester.pumpWidget(_host(const Locale('en')));
    await tester.pumpAndSettle();
    expect(WebDaemonNotice.installerUrl(), isEmpty); // the test host is not a browser
    expect(find.text('Manage paired devices'), findsOneWidget);
    expect(find.text('Get the desktop app'), findsNothing);
  });

  test('the installer sits next to the app on the same host, outside /app', () {
    expect(webAppBaseUrl('https://web.aureon-ai.tech', '/app/'), 'https://web.aureon-ai.tech/app');
    final uri = Uri.parse(webAppBaseUrl('https://web.aureon-ai.tech', '/app/'));
    expect('${uri.scheme}://${uri.authority}/desktop-ide/install', 'https://web.aureon-ai.tech/desktop-ide/install');
  });
}
