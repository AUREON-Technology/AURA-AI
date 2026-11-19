/// Mobile sign-in gaps found in the 2026-11-16 auth review:
///
/// * A device with no biometric enrolled had to sign in with Google on every launch, although a
///   valid refresh token was on disk: the saved session was only ever resumed after a
///   fingerprint or face check. It is now resumed directly when there is nothing to prompt for.
/// * Google sign-in "succeeded" without asking the backend, so an account the backend refuses
///   got in on a session nothing could renew. The exchange now runs at sign-in.
/// * A refresh token left by an earlier session survived a sign-in as a different account, so
///   API calls kept going out as the old account while the local data was wiped for the new one.
library;

import 'dart:async';
import 'dart:convert';

import 'package:aura_client/core/auth/refresh_token_store.dart';
import 'package:aura_client/core/auth/sign_in_refused.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/features/auth/presentation/login_page.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:local_auth/local_auth.dart';

class _MemoryRefreshTokenStore implements RefreshTokenStore {
  String? token;
  _MemoryRefreshTokenStore([this.token]);

  @override
  Future<String?> read() async => token;

  @override
  Future<void> write(String value) async => token = value;

  @override
  Future<void> delete() async => token = null;
}

/// A local gateway: debug builds aimed at one send the dev attestation token, so the exchange
/// below never reaches a real attestation SDK or the network.
class _LocalBackend extends BackendUrlNotifier {
  @override
  String build() => 'http://127.0.0.1:8000';
}

class _FakeLocalAuth implements LocalAuthentication {
  _FakeLocalAuth({required this.enrolled});

  final bool enrolled;
  int prompts = 0;

  @override
  Future<bool> get canCheckBiometrics async => true;

  @override
  Future<List<BiometricType>> getAvailableBiometrics() async =>
      enrolled ? const [BiometricType.fingerprint] : const [];

  @override
  Future<bool> authenticate({
    required String localizedReason,
    Iterable<dynamic> authMessages = const [],
    AuthenticationOptions options = const AuthenticationOptions(),
  }) async {
    prompts++;
    return false;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

/// Signed out, with a saved session on the device.
class _SavedSession extends AuthNotifier {
  int resumed = 0;

  @override
  FutureOr<UserProfile?> build() => null;

  @override
  Future<bool> hasCachedCredentials() async => true;

  @override
  Future<void> signInCached() async => resumed++;
}

class _Expired extends SessionExpiredNotifier {
  @override
  bool build() => true;
}

void main() {
  group('cachedSessionUnlock', () {
    test('resumes directly when no biometric is enrolled', () {
      expect(
        cachedSessionUnlock(hasCachedSession: true, sessionExpired: false, biometricsEnrolled: false),
        CachedSessionUnlock.direct,
      );
    });

    test('asks for a biometric first when one is enrolled', () {
      expect(
        cachedSessionUnlock(hasCachedSession: true, sessionExpired: false, biometricsEnrolled: true),
        CachedSessionUnlock.biometric,
      );
    });

    test('does nothing without a saved session, or right after the server ended it', () {
      for (final enrolled in [true, false]) {
        expect(
          cachedSessionUnlock(hasCachedSession: false, sessionExpired: false, biometricsEnrolled: enrolled),
          CachedSessionUnlock.none,
        );
        expect(
          cachedSessionUnlock(hasCachedSession: true, sessionExpired: true, biometricsEnrolled: enrolled),
          CachedSessionUnlock.none,
        );
      }
    });
  });

  group('LoginPage on open', () {
    Future<(_SavedSession, _FakeLocalAuth)> open(
      WidgetTester tester, {
      required bool enrolled,
      bool expired = false,
    }) async {
      final auth = _SavedSession();
      final localAuth = _FakeLocalAuth(enrolled: enrolled);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            authProvider.overrideWith(() => auth),
            localAuthenticationProvider.overrideWithValue(localAuth),
            if (expired) sessionExpiredProvider.overrideWith(_Expired.new),
          ],
          child: MaterialApp(
            theme: AuraThemeV2.light,
            localizationsDelegates: AppLocalizations.localizationsDelegates,
            supportedLocales: AppLocalizations.supportedLocales,
            home: const LoginPage(),
          ),
        ),
      );
      await tester.pump();
      await tester.pump();
      return (auth, localAuth);
    }

    testWidgets('resumes the saved session when no biometric is enrolled', (tester) async {
      final (auth, localAuth) = await open(tester, enrolled: false);
      expect(auth.resumed, 1);
      expect(localAuth.prompts, 0);
    });

    testWidgets('prompts for a biometric when one is enrolled', (tester) async {
      final (auth, localAuth) = await open(tester, enrolled: true);
      expect(localAuth.prompts, 1);
      expect(auth.resumed, 0, reason: 'the fake prompt fails, so nothing is resumed');
    });

    testWidgets('resumes nothing after the server ended the session', (tester) async {
      final (auth, localAuth) = await open(tester, enrolled: false, expired: true);
      expect(auth.resumed, 0);
      expect(localAuth.prompts, 0);
    });
  });

  group('TokenManager.startSession', () {
    late _MemoryRefreshTokenStore store;
    late List<String> calls;

    TokenManager manager(Future<http.Response> Function(http.Request) token) {
      calls = [];
      final client = MockClient((request) async {
        final path = request.url.path;
        calls.add(path.substring(path.lastIndexOf('/')));
        if (path.endsWith('/auth/token')) return token(request);
        if (path.endsWith('/auth/revoke')) {
          calls.add('revoked ${jsonDecode(request.body)['refresh_token']}');
          return http.Response('{"status":"revoked"}', 200);
        }
        return http.Response('{}', 404);
      });
      final container = ProviderContainer(
        overrides: [
          backendUrlProvider.overrideWith(_LocalBackend.new),
          tokenManagerProvider.overrideWith(
            (ref) => TokenManager(ref, httpClient: client, store: store),
          ),
        ],
      );
      addTearDown(container.dispose);
      return container.read(tokenManagerProvider);
    }

    http.Response issued(String access, String refresh) => http.Response(
          jsonEncode({'access_token': access, 'refresh_token': refresh, 'expires_in': 900}),
          200,
        );

    test('exchanges the new Google token and revokes the previous session', () async {
      store = _MemoryRefreshTokenStore('refresh-old-account');
      final tm = manager((request) async {
        expect(request.headers['Authorization'], 'Bearer google-new');
        expect(request.headers['X-Device-Attestation'], isNotEmpty);
        return issued('access-new', 'refresh-new');
      });

      expect(await tm.startSession('google-new'), SessionStart.started);
      expect(store.token, 'refresh-new');
      expect(calls, contains('revoked refresh-old-account'));
      expect(calls, isNot(contains('/refresh')),
          reason: 'the old account\'s refresh token must not be what starts this session');
      expect(await tm.getValidAccessToken(), 'access-new');
    });

    test('a refused account stops the sign-in and leaves the previous session alone', () async {
      store = _MemoryRefreshTokenStore('refresh-old-account');
      final tm = manager((_) async => http.Response('{"detail":"Unrecognized audience"}', 401));

      expect(await tm.startSession('google-new'), SessionStart.refused);
      expect(store.token, 'refresh-old-account');
      expect(calls, isNot(contains('/revoke')));
    });

    test('an unreachable server lets the sign-in through and forgets the old token', () async {
      store = _MemoryRefreshTokenStore('refresh-old-account');
      final tm = manager((_) async => http.Response('upstream down', 503));

      expect(await tm.startSession('google-new'), SessionStart.unreachable);
      expect(store.token, isNull,
          reason: 'a later refresh would otherwise renew the previous account\'s session');
      expect(calls, isNot(contains('/revoke')),
          reason: 'a revoke to a server that is not answering would only wait out its timeout');
    });

    test('a rejected attestation lets the sign-in through and reports the device', () async {
      store = _MemoryRefreshTokenStore('refresh-old-account');
      final tm = manager(
        (_) async => http.Response('{"detail":"Device attestation validation failed"}', 403),
      );

      expect(await tm.startSession('google-new'), SessionStart.deviceUnverified);
      expect(store.token, isNull);
      expect(calls, contains('revoked refresh-old-account'));
    });

    test('a first sign-in with no earlier session revokes nothing', () async {
      store = _MemoryRefreshTokenStore();
      final tm = manager((_) async => issued('access-1', 'refresh-1'));

      expect(await tm.startSession('google-1'), SessionStart.started);
      expect(calls, ['/token']);
    });
  });

  test('a plan refusal says a Pro or Team plan is needed, not just "refused" (WP11)', () {
    final failure = AuraFailure.from(const SignInRefused('needs a plan', true));
    expect(failure.kind, AuraFailureKind.forbidden);
    expect(failure.title, 'The desktop app needs a Pro or Team plan');
    expect(failure.message, contains('Upgrade your plan'));
    expect(failure.canRetry, isFalse);
  });

  test('a refused sign-in reads as a refusal, with no retry offered', () {
    final failure = AuraFailure.from(const SignInRefused());
    expect(failure.kind, AuraFailureKind.forbidden);
    expect(failure.title, 'AURA did not accept this sign-in');
    expect(failure.canRetry, isFalse);
  });
}
