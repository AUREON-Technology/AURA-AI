/// SaaS P1-B: the sign-in screen offers Google, Apple (where Apple requires or supports it) and
/// email/password, and words every failure the identity provider can report.
library;

import 'dart:async';

import 'package:aura_client/core/auth/identity_service.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/features/auth/presentation/login_page.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:local_auth/local_auth.dart';

class _NoBiometrics implements LocalAuthentication {
  @override
  Future<bool> get canCheckBiometrics async => false;

  @override
  Future<List<BiometricType>> getAvailableBiometrics() async => const [];

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

/// Records what the page asked for; [failWith] makes the next sign-in end in that error.
class _RecordingAuth extends AuthNotifier {
  final List<String> calls = [];
  Object? failWith;

  @override
  FutureOr<UserProfile?> build() => null;

  @override
  Future<bool> hasCachedCredentials() async => false;

  void _finish() {
    final error = failWith;
    state = error == null ? const AsyncValue.data(null) : AsyncValue.error(error, StackTrace.empty);
  }

  @override
  Future<void> signInWithGoogle() async {
    calls.add('google');
    _finish();
  }

  @override
  Future<void> signInWithApple() async {
    calls.add('apple');
    _finish();
  }

  @override
  Future<void> signInWithEmail(String email, String password) async {
    calls.add('email:$email:$password');
    _finish();
  }

  @override
  Future<void> registerWithEmail(String email, String password, {String? displayName}) async {
    calls.add('register:$email:$password:$displayName');
    _finish();
  }

  @override
  Future<void> sendPasswordReset(String email) async => calls.add('reset:$email');
}

Future<_RecordingAuth> _open(WidgetTester tester) async {
  final auth = _RecordingAuth();
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        authProvider.overrideWith(() => auth),
        localAuthenticationProvider.overrideWithValue(_NoBiometrics()),
      ],
      child: MaterialApp(
        theme: AuraThemeV2.light,
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: const LoginPage(),
      ),
    ),
  );
  await tester.pumpAndSettle();
  return auth;
}

Future<void> _openEmailForm(WidgetTester tester) async {
  await tester.ensureVisible(find.text('Continue with email'));
  await tester.tap(find.text('Continue with email'));
  await tester.pumpAndSettle();
}

Future<void> _tapText(WidgetTester tester, String text) async {
  await tester.ensureVisible(find.text(text));
  await tester.tap(find.text(text));
  await tester.pumpAndSettle();
}

Future<void> _enter(WidgetTester tester, String label, String text) async {
  await tester.enterText(find.widgetWithText(TextField, label), text);
}

Future<void> _press(WidgetTester tester, String label) async {
  final button = find.widgetWithText(AuraButton, label);
  await tester.ensureVisible(button);
  await tester.tap(button);
  await tester.pumpAndSettle();
}

void main() {
  group('appleSignInOffered', () {
    test('iOS, macOS and the web offer Apple', () {
      expect(appleSignInOffered(platform: TargetPlatform.iOS, isWeb: false), isTrue);
      expect(appleSignInOffered(platform: TargetPlatform.macOS, isWeb: false), isTrue);
      expect(appleSignInOffered(platform: TargetPlatform.android, isWeb: true), isTrue);
    });
    test('Android, Linux and Windows apps do not', () {
      expect(appleSignInOffered(platform: TargetPlatform.android, isWeb: false), isFalse);
      expect(appleSignInOffered(platform: TargetPlatform.linux, isWeb: false), isFalse);
      expect(appleSignInOffered(platform: TargetPlatform.windows, isWeb: false), isFalse);
    });
  });

  testWidgets('shows Apple on iOS and calls the Apple sign-in', (tester) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.iOS;
    try {
      final auth = await _open(tester);
      expect(find.text('Continue with Apple'), findsOneWidget);
      await _press(tester, 'Continue with Apple');
      expect(auth.calls, ['apple']);
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('does not show Apple on Android', (tester) async {
    await _open(tester);
    expect(find.text('Continue with Apple'), findsNothing);
    expect(find.text('Continue with email'), findsOneWidget);
  });

  testWidgets('email sign-in sends the trimmed address and the password', (tester) async {
    final auth = await _open(tester);
    await _openEmailForm(tester);
    await _enter(tester, 'Email', '  ann@example.com ');
    await _enter(tester, 'Password', 'correct horse');
    await _press(tester, 'Sign in');
    expect(auth.calls, ['email:ann@example.com:correct horse']);
  });

  testWidgets('registration validates before calling the service', (tester) async {
    final auth = await _open(tester);
    await _openEmailForm(tester);
    await _tapText(tester, 'New to AURA? Create an account');

    await _enter(tester, 'Name', 'Ann');
    await _enter(tester, 'Email', 'not-an-email');
    await _enter(tester, 'Password', 'short');
    await _enter(tester, 'Confirm password', 'different');
    await _press(tester, 'Create account');
    expect(auth.calls, isEmpty);
    expect(find.text('Enter a valid email address.'), findsOneWidget);
    expect(find.text('Use at least 8 characters.'), findsOneWidget);
    expect(find.text("The passwords don't match."), findsOneWidget);

    await _enter(tester, 'Email', 'ann@example.com');
    await _enter(tester, 'Password', 'correct horse');
    await _enter(tester, 'Confirm password', 'correct horse');
    await _press(tester, 'Create account');
    expect(auth.calls, ['register:ann@example.com:correct horse:Ann']);
  });

  testWidgets('forgot password never reveals whether the account exists', (tester) async {
    final auth = await _open(tester);
    await _openEmailForm(tester);
    await _tapText(tester, 'Forgot password?');
    await _enter(tester, 'Email', 'nobody@example.com');
    await _press(tester, 'Send reset link');
    expect(auth.calls, ['reset:nobody@example.com']);
    expect(find.text("If an account exists for that address, we've sent a reset link."), findsOneWidget);
  });

  testWidgets('a wrong password is worded, not shown raw', (tester) async {
    final auth = await _open(tester);
    auth.failWith = const IdentityError(IdentityErrorKind.wrongCredentials, 'INVALID_LOGIN_CREDENTIALS');
    await _openEmailForm(tester);
    await _enter(tester, 'Email', 'ann@example.com');
    await _enter(tester, 'Password', 'nope nope');
    await _press(tester, 'Sign in');
    // The user-facing line is the worded one. (Debug builds also open the developer panel with
    // the provider's raw detail -- by design, and never in release.)
    expect(find.text("That email and password don't match an account."), findsOneWidget);
  });

  testWidgets('another sign-in method on the same email is explained', (tester) async {
    final auth = await _open(tester);
    auth.failWith = const IdentityError(IdentityErrorKind.accountExistsWithDifferentMethod, 'x');
    await _press(tester, 'Continue with email');
    await _enter(tester, 'Email', 'ann@example.com');
    await _enter(tester, 'Password', 'correct horse');
    await _press(tester, 'Sign in');
    expect(find.textContaining('already uses another sign-in method'), findsOneWidget);
  });

  testWidgets('a cancelled sign-in shows nothing', (tester) async {
    final auth = await _open(tester);
    await _press(tester, 'Continue with email');
    await _enter(tester, 'Email', 'ann@example.com');
    await _enter(tester, 'Password', 'correct horse');
    await _press(tester, 'Sign in');
    expect(auth.calls, hasLength(1));
    expect(find.byIcon(Icons.error_outline), findsNothing);
    expect(find.textContaining('match an account'), findsNothing);
  });
}
