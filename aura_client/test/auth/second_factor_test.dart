// SaaS P1-I: the second sign-in step's code dialog.

import 'dart:async';

import 'package:aura_client/core/auth/identity_service.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/auth/presentation/second_factor_dialog.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const _ann = IdentityUser(uid: 'u1', email: 'ann@example.com', displayName: 'Ann', emailVerified: true, provider: 'password');

void main() {
  late List<String> codes;
  late Object? result;

  SecondFactorRequired challenge({bool totp = true}) => SecondFactorRequired(
        totp: totp,
        resolve: (code) async {
          codes.add(code);
          if (code != '123456') throw const IdentityError(IdentityErrorKind.wrongCode, 'no');
          return _ann;
        },
      );

  Future<void> open(WidgetTester tester, SecondFactorRequired c) async {
    codes = [];
    result = 'not closed';
    await tester.pumpWidget(MaterialApp(
      theme: AuraThemeV2.light,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: Builder(
        builder: (context) => TextButton(
          onPressed: () async => result = await showSecondFactorDialog(context, c),
          child: const Text('go'),
        ),
      ),
    ));
    await tester.tap(find.text('go'));
    await tester.pumpAndSettle();
  }

  testWidgets('a wrong code keeps the dialog open; the right one returns the user', (tester) async {
    await open(tester, challenge());
    await tester.enterText(find.byKey(const Key('two-step-code')), '000000');
    await tester.tap(find.byKey(const Key('two-step-verify')));
    await tester.pumpAndSettle();
    expect(find.text("That code didn't match. Use the newest code from your authenticator app."), findsOneWidget);
    expect(result, 'not closed');

    await tester.enterText(find.byKey(const Key('two-step-code')), '123456');
    await tester.tap(find.byKey(const Key('two-step-verify')));
    await tester.pumpAndSettle();
    expect(result, same(_ann));
    expect(codes, ['000000', '123456']);
  });

  testWidgets('cancel returns nothing', (tester) async {
    await open(tester, challenge());
    await tester.tap(find.byKey(const Key('two-step-cancel')));
    await tester.pumpAndSettle();
    expect(result, isNull);
    expect(codes, isEmpty);
  });

  testWidgets('an unsupported second step says so and offers no code field', (tester) async {
    await open(tester, challenge(totp: false));
    expect(find.byKey(const Key('two-step-unsupported')), findsOneWidget);
    expect(find.byKey(const Key('two-step-code')), findsNothing);
    expect(find.byKey(const Key('two-step-verify')), findsNothing);
  });

  testWidgets('system back cannot close the dialog while a code is being checked', (tester) async {
    // Review I#4: a pop mid-check would sign the person out while Firebase then signs them in.
    final pending = Completer<IdentityUser>();
    await open(tester, SecondFactorRequired(totp: true, resolve: (code) => pending.future));
    await tester.enterText(find.byKey(const Key('two-step-code')), '123456');
    await tester.tap(find.byKey(const Key('two-step-verify')));
    await tester.pump();
    await tester.binding.handlePopRoute();
    await tester.pump();
    expect(find.byKey(const Key('two-step-code')), findsOneWidget, reason: 'still open while checking');
    pending.complete(_ann);
    await tester.pumpAndSettle();
    expect(result, same(_ann));
  });
}
