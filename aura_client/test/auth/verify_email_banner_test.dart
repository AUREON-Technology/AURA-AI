/// SaaS P1-B: an email/password account sees how to verify its address until it does; teams
/// stay closed to it until then (the backend checks).
library;

import 'package:aura_client/core/auth/identity_service.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/auth/presentation/verify_email_banner.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

class _Identity implements IdentityService {
  _Identity(this.current);

  @override
  IdentityUser? current;
  int resends = 0;

  @override
  Future<void> resendVerification() async => resends++;

  int reloads = 0;

  @override
  Future<IdentityUser?> reload() async {
    reloads++;
    return current;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

IdentityUser _user({required bool verified, String provider = 'password'}) => IdentityUser(
      uid: 'u1', email: 'ann@example.com', displayName: 'Ann', emailVerified: verified, provider: provider,
    );

Future<void> _pump(WidgetTester tester, _Identity identity) async {
  await tester.pumpWidget(ProviderScope(
    overrides: [identityServiceProvider.overrideWithValue(identity)],
    child: MaterialApp(
      theme: AuraThemeV2.light,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: const Scaffold(body: VerifyEmailBanner()),
    ),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('an unverified email account is told how to verify, and can resend', (tester) async {
    final identity = _Identity(_user(verified: false));
    await _pump(tester, identity);
    expect(find.textContaining('verify your email'), findsOneWidget);
    await tester.tap(find.text('Resend'));
    await tester.pumpAndSettle();
    expect(identity.resends, 1);
  });

  testWidgets("\"I've verified\" checks with the identity provider", (tester) async {
    final identity = _Identity(_user(verified: false));
    await _pump(tester, identity);
    await tester.tap(find.text("I've verified"));
    await tester.pumpAndSettle();
    expect(identity.reloads, 1);
  });

  testWidgets('a verified account sees nothing', (tester) async {
    await _pump(tester, _Identity(_user(verified: true)));
    expect(find.textContaining('verify your email'), findsNothing);
  });

  testWidgets('nobody signed in through the identity layer sees nothing', (tester) async {
    await _pump(tester, _Identity(null));
    expect(find.byType(AuraBanner), findsNothing);
  });
}
