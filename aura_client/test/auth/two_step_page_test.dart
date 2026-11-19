// SaaS P1-I: the Two-step verification settings page.

import 'package:aura_client/core/auth/identity_service.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/auth/presentation/two_step_page.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

class _Identity implements IdentityService {
  _Identity({this.verified = true});

  final bool verified;
  final factors = <SecondFactor>[];
  final calls = <String>[];
  IdentityError? failWith;

  @override
  IdentityUser? get current => IdentityUser(
      uid: 'u1', email: 'ann@example.com', displayName: 'Ann', emailVerified: verified, provider: 'password');

  @override
  Future<List<SecondFactor>> secondFactors() async => List.of(factors);

  @override
  Future<TotpEnrollment> startTotpEnrollment() async {
    calls.add('start');
    if (failWith != null) throw failWith!;
    return const TotpEnrollment(secretKey: 'JBSWY3DPEHPK3PXP', otpauthUrl: 'otpauth://totp/AURA:ann?secret=JBSWY3DPEHPK3PXP');
  }

  @override
  Future<void> finishTotpEnrollment(TotpEnrollment enrollment, String code) async {
    calls.add('finish:$code');
    if (code != '123456') throw const IdentityError(IdentityErrorKind.wrongCode, 'no');
    factors.add(SecondFactor(uid: 'f1', displayName: 'Authenticator app', enrolledAt: DateTime.utc(2026, 11, 18)));
  }

  @override
  Future<void> removeSecondFactor(String uid) async {
    calls.add('remove:$uid');
    factors.removeWhere((f) => f.uid == uid);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnimplementedError('${invocation.memberName}');
}

Future<void> _pump(WidgetTester tester, _Identity identity) async {
  await tester.pumpWidget(ProviderScope(
    key: UniqueKey(),
    overrides: [identityServiceProvider.overrideWithValue(identity)],
    child: MaterialApp(
      theme: AuraThemeV2.light,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: const TwoStepPage(),
    ),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('an unverified email is told why, and offered no set-up', (tester) async {
    final identity = _Identity(verified: false);
    await _pump(tester, identity);
    expect(find.text('Verify your email address first, then come back to turn this on.'), findsOneWidget);
    expect(find.byKey(const Key('two-step-setup')), findsNothing);
  });

  testWidgets('set up: the key is shown, a wrong code is refused, the right one turns it on', (tester) async {
    final identity = _Identity();
    await _pump(tester, identity);
    await tester.tap(find.byKey(const Key('two-step-setup')));
    await tester.pumpAndSettle();
    expect(find.text('JBSWY3DPEHPK3PXP'), findsOneWidget);

    await tester.enterText(find.byKey(const Key('two-step-enroll-code')), '000000');
    await tester.tap(find.byKey(const Key('two-step-enroll-verify')));
    await tester.pumpAndSettle();
    expect(find.text("That code didn't match. Use the newest code from your authenticator app."), findsOneWidget);
    expect(identity.factors, isEmpty);

    await tester.enterText(find.byKey(const Key('two-step-enroll-code')), '123456');
    await tester.tap(find.byKey(const Key('two-step-enroll-verify')));
    await tester.pumpAndSettle();
    expect(identity.factors.single.uid, 'f1');
    expect(find.byKey(const Key('two-step-on')), findsOneWidget);
    expect(find.text('JBSWY3DPEHPK3PXP'), findsNothing, reason: 'the secret is shown only while setting up');
  });

  testWidgets('turning it off asks first, then removes the factor', (tester) async {
    final identity = _Identity()
      ..factors.add(SecondFactor(uid: 'f1', displayName: 'Authenticator app', enrolledAt: DateTime.utc(2026, 11, 18)));
    await _pump(tester, identity);
    await tester.tap(find.byKey(const Key('two-step-off')));
    await tester.pumpAndSettle();
    expect(identity.calls, isEmpty, reason: 'nothing removed before the confirmation');
    await tester.tap(find.text('Turn off').last);
    await tester.pumpAndSettle();
    expect(identity.calls, ['remove:f1']);
    expect(find.byKey(const Key('two-step-setup')), findsOneWidget);
  });

  testWidgets('a stale sign-in is told to sign in again', (tester) async {
    final identity = _Identity()..failWith = const IdentityError(IdentityErrorKind.reauthRequired, 'recent');
    await _pump(tester, identity);
    await tester.tap(find.byKey(const Key('two-step-setup')));
    await tester.pumpAndSettle();
    expect(find.text('For your security, sign out and sign in again, then try once more.'), findsOneWidget);
  });
}
