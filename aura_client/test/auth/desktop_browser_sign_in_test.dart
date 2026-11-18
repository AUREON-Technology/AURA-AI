/// Module readiness WP12: the desktop app (Linux, Windows) signs in with Firebase -- Google, Apple
/// or email -- through the web app's `/desktop-sign-in` page, which hands a one-time code back to
/// the app's loopback listener.
///
/// Covers the three client pieces: the web page (what it accepts, what it refuses, that it signs
/// the browser out again and starts no AURA web session), the desktop `AuthNotifier` step (a real
/// loopback hand-off against a local fake server, its 403 becoming a refusal), and where the
/// login page offers the button and the router lets the page open.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:aura_client/core/auth/desktop_handoff_client.dart';
import 'package:aura_client/core/auth/desktop_oauth_client.dart';
import 'package:aura_client/core/auth/identity_service.dart';
import 'package:aura_client/core/auth/refresh_token_store.dart';
import 'package:aura_client/core/auth/sign_in_refused.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/app_links.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/routing/router.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:aura_client/features/auth/presentation/desktop_sign_in_page.dart';
import 'package:aura_client/features/auth/presentation/login_page.dart';
import 'package:aura_client/l10n/l10n.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart' show Override;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:local_auth/local_auth.dart';

const _redirect = 'http://127.0.0.1:53682/';
const _state = 'abcdefghijklmnopqrstuvwx';
const _challenge = 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM'; // RFC 7636 appendix B

const _validQuery = {'redirect_uri': _redirect, 'state': _state, 'code_challenge': _challenge};

const _ann = IdentityUser(
  uid: 'firebase-ann',
  email: 'ann@example.com',
  displayName: 'Ann',
  emailVerified: true,
  provider: 'google.com',
);

class _MemoryRefreshTokenStore implements RefreshTokenStore {
  String? token;

  @override
  Future<String?> read() async => token;

  @override
  Future<void> write(String value) async => token = value;

  @override
  Future<void> delete() async => token = null;
}

class _Backend extends BackendUrlNotifier {
  _Backend(this.url);

  final String url;

  @override
  String build() => url;
}

/// Records every call into one shared [log], so a test can check the order of sign-out and the
/// return to the desktop app.
class _FakeIdentity implements IdentityService {
  _FakeIdentity(this.log, {this.error});

  final List<String> log;
  final IdentityError? error;

  @override
  IdentityUser? current;

  Future<IdentityUser> _signIn(String how) async {
    log.add(how);
    if (error != null) throw error!;
    return current = _ann;
  }

  @override
  Future<IdentityUser> signInWithGoogle() => _signIn('google');

  @override
  Future<IdentityUser> signInWithApple() => _signIn('apple');

  @override
  Future<IdentityUser> signInWithEmail(String email, String password) =>
      _signIn('email:$email:$password');

  @override
  Future<String?> idToken({bool forceRefresh = false}) async =>
      current == null ? null : 'firebase-id-token';

  @override
  Future<void> signOut() async {
    log.add('signOut');
    current = null;
  }

  @override
  Future<void> sendPasswordReset(String email) async => log.add('reset:$email');

  @override
  Future<void> initialize() async {}

  @override
  Future<IdentityUser?> restore() async => current;

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _NoBiometrics implements LocalAuthentication {
  @override
  Future<bool> get canCheckBiometrics async => false;

  @override
  Future<List<BiometricType>> getAvailableBiometrics() async => const [];

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _RecordingAuth extends AuthNotifier {
  final List<String> calls = [];

  @override
  FutureOr<UserProfile?> build() => null;

  @override
  Future<bool> hasCachedCredentials() async => false;

  @override
  Future<void> signInWithDesktopOAuth() async => calls.add('desktop-google');

  @override
  Future<void> signInWithDesktopBrowser() async => calls.add('desktop-browser');
}

/// The web page, with the hand-off answered by [handoff] and the return to the desktop app
/// recorded in [log] instead of leaving the test.
Future<void> _openPage(
  WidgetTester tester, {
  required List<String> log,
  Map<String, String> query = _validQuery,
  IdentityError? signInError,
  http.Response Function(http.Request request)? handoff,
  bool isWeb = true,
  List<http.Request>? requests,
}) async {
  final client = MockClient((request) async {
    requests?.add(request);
    log.add('handoff');
    return handoff?.call(request) ??
        http.Response(jsonEncode({'code': 'one-time-code', 'expires_in': 60}), 200);
  });
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        backendUrlProvider.overrideWith(() => _Backend('https://api.example.test')),
        identityServiceProvider.overrideWithValue(_FakeIdentity(log, error: signInError)),
        desktopHandoffClientProvider.overrideWith(
          (ref) => DesktopHandoffClient(ref, httpClient: client),
        ),
        desktopHandoffReturnProvider.overrideWithValue((uri) async {
          log.add('return $uri');
          return true;
        }),
      ],
      child: MaterialApp(
        theme: AuraThemeV2.light,
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: DesktopSignInPage(query: query, isWeb: isWeb),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

Future<void> _press(WidgetTester tester, String label) async {
  final button = find.widgetWithText(AuraButton, label);
  await tester.ensureVisible(button);
  await tester.tap(button);
  await tester.pumpAndSettle();
}

String _jwt(Map<String, Object?> claims) {
  String part(Object value) => base64Url.encode(utf8.encode(jsonEncode(value))).replaceAll('=', '');
  return '${part({'alg': 'HS256', 'typ': 'JWT'})}.${part(claims)}.signature';
}

void main() {
  final en = lookupAppLocalizations(const Locale('en'));

  group('DesktopHandoffRequest.fromQuery', () {
    test('accepts what the desktop app sends', () {
      final request = DesktopHandoffRequest.fromQuery(_validQuery)!;
      expect(request.returnUri('c0de').toString(), '$_redirect?code=c0de&state=$_state');
    });

    test('refuses any redirect but http://127.0.0.1:<port>/', () {
      for (final redirect in [
        'https://127.0.0.1:53682/',
        'http://localhost:53682/',
        'http://127.0.0.1.evil.example:53682/',
        'http://evil.example/',
        'http://127.0.0.1:53682/callback',
        'http://127.0.0.1:53682',
        'http://127.0.0.1/',
        'http://127.0.0.1:0/',
        'http://127.0.0.1:99999/',
        'http://user@127.0.0.1:53682/',
        '',
      ]) {
        expect(
          DesktopHandoffRequest.fromQuery({..._validQuery, 'redirect_uri': redirect}),
          isNull,
          reason: redirect,
        );
      }
    });

    test('refuses a missing or malformed state or challenge', () {
      expect(DesktopHandoffRequest.fromQuery({..._validQuery}..remove('state')), isNull);
      expect(DesktopHandoffRequest.fromQuery({..._validQuery, 'state': 'a b'}), isNull);
      expect(DesktopHandoffRequest.fromQuery({..._validQuery}..remove('code_challenge')), isNull);
      expect(DesktopHandoffRequest.fromQuery({..._validQuery, 'code_challenge': 'plain'}), isNull);
    });
  });

  group('the /desktop-sign-in page', () {
    testWidgets('signs in, hands the Firebase token over, signs out, then returns to the app',
        (tester) async {
      final log = <String>[];
      final requests = <http.Request>[];
      await _openPage(tester, log: log, requests: requests);

      await _press(tester, en.loginGoogle);

      expect(log, [
        'google',
        'handoff',
        'signOut',
        'return $_redirect?code=one-time-code&state=$_state',
      ]);
      final request = requests.single;
      expect(request.url.toString(), 'https://api.example.test/api/v3/auth/desktop/handoff');
      expect(request.headers['Authorization'], 'Bearer firebase-id-token');
      expect(jsonDecode(request.body), {'code_challenge': _challenge, 'redirect_uri': _redirect});
      expect(find.text(en.desktopSignInDone), findsOneWidget);
    });

    testWidgets('shows the server\'s reason, still signs out, and does not return', (tester) async {
      final log = <String>[];
      await _openPage(
        tester,
        log: log,
        handoff: (_) => http.Response(
          jsonEncode({'detail': 'The redirect must be http://127.0.0.1:<port>/.'}),
          400,
        ),
      );

      await _press(tester, en.loginGoogle);

      expect(log, ['google', 'handoff', 'signOut']);
      expect(find.text('The redirect must be http://127.0.0.1:<port>/.'), findsOneWidget);
      // The choices are still there for another try.
      expect(find.widgetWithText(AuraButton, en.loginGoogle), findsOneWidget);
    });

    testWidgets('a server error without a reason is worded with its status', (tester) async {
      final log = <String>[];
      await _openPage(tester, log: log, handoff: (_) => http.Response('<html>oops</html>', 502));

      await _press(tester, en.loginApple);

      expect(log, ['apple', 'handoff', 'signOut']);
      expect(find.text(en.desktopSignInServerFailed(502)), findsOneWidget);
    });

    testWidgets('closing the provider window is not an error and hands nothing over',
        (tester) async {
      final log = <String>[];
      await _openPage(
        tester,
        log: log,
        signInError: const IdentityError(IdentityErrorKind.cancelled, 'popup-closed-by-user'),
      );

      await _press(tester, en.loginGoogle);

      expect(log, ['google']);
      expect(find.byKey(const Key('desktop-sign-in-error')), findsNothing);
    });

    testWidgets('email signs in without offering account creation', (tester) async {
      final log = <String>[];
      await _openPage(tester, log: log);

      await _press(tester, en.loginEmail);
      expect(find.text(en.emailSwitchToRegister), findsNothing);
      await tester.enterText(find.widgetWithText(TextField, en.emailFieldLabel), 'ann@example.com');
      await tester.enterText(find.widgetWithText(TextField, en.passwordFieldLabel), 'correct horse');
      await _press(tester, en.emailSignInAction);

      expect(log.first, 'email:ann@example.com:correct horse');
      expect(log.last, 'return $_redirect?code=one-time-code&state=$_state');
    });

    testWidgets('refuses a link that would send the code anywhere but this computer',
        (tester) async {
      final log = <String>[];
      await _openPage(
        tester,
        log: log,
        query: {..._validQuery, 'redirect_uri': 'https://evil.example/'},
      );

      expect(find.byKey(const Key('desktop-sign-in-invalid')), findsOneWidget);
      expect(find.text(en.desktopSignInInvalidLink), findsOneWidget);
      expect(find.widgetWithText(AuraButton, en.loginGoogle), findsNothing);
      expect(find.widgetWithText(AuraButton, en.loginEmail), findsNothing);
      expect(log, isEmpty);
    });

    testWidgets('outside a web browser it only points back to the sign-in screen', (tester) async {
      final log = <String>[];
      await _openPage(tester, log: log, isWeb: false);

      expect(find.text(en.desktopSignInWebOnly), findsOneWidget);
      expect(find.widgetWithText(AuraButton, en.loginGoogle), findsNothing);
    });
  });

  group('routing', () {
    test('the page opens signed out, and is never sent to onboarding or the landing screen',
        () async {
      for (final (loggedIn, onboarded, desktop) in [
        (false, null, false),
        (false, null, true),
        (true, false, false),
        (true, true, true),
      ]) {
        expect(
          await resolveAppRedirect(
            isLoggedIn: loggedIn,
            onboarded: onboarded,
            location: desktopSignInPath,
            desktop: desktop,
          ),
          isNull,
        );
      }
      // Nothing else became public.
      expect(
        await resolveAppRedirect(
            isLoggedIn: false, onboarded: null, location: '/invite', desktop: false),
        '/login',
      );
    });

    test('a web build opened at /desktop-sign-in starts there, query intact', () {
      expect(
        webDeepLinkLocation(Uri.parse('https://app.example.test/desktop-sign-in?state=s&x=1')),
        '/desktop-sign-in?state=s&x=1',
      );
      expect(webDeepLinkLocation(Uri.parse('https://app.example.test/')), isNull);
      expect(webDeepLinkLocation(Uri.parse('https://app.example.test/#/desktop-sign-in')), isNull);
    });

    // The invite link (`inviteLinkFor`) is a real path too. Under the hash strategy it used to
    // reach the router as `/`: the page never opened and the token was lost.
    test('a web build opened from an invite link starts at the invite, token intact', () {
      expect(
        webDeepLinkLocation(Uri.parse('https://app.example.test/invite?token=tok-123')),
        '/invite?token=tok-123',
      );
      expect(webDeepLinkLocation(Uri.parse('https://app.example.test/invite')), '/invite');
      expect(webDeepLinkLocation(Uri.parse('https://app.example.test/invitex?token=t')), isNull);
      expect(webDeepLinkLocation(Uri.parse('https://app.example.test/today?token=t')), isNull);
    });
  });

  group('the desktop login button', () {
    test('only Linux/Windows builds that know the web app offer it', () {
      expect(desktopBrowserSignInOffered(nativeSignIn: false, appBaseUrl: 'https://a.test'), isTrue);
      expect(desktopBrowserSignInOffered(nativeSignIn: false, appBaseUrl: ''), isFalse);
      expect(desktopBrowserSignInOffered(nativeSignIn: true, appBaseUrl: 'https://a.test'), isFalse);
    });

    Future<_RecordingAuth> openLogin(
      WidgetTester tester, {
      required String appBaseUrl,
      List<Override> extra = const [],
    }) async {
      final auth = _RecordingAuth();
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            authProvider.overrideWith(() => auth),
            localAuthenticationProvider.overrideWithValue(_NoBiometrics()),
            appBaseUrlProvider.overrideWithValue(appBaseUrl),
            ...extra,
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

    // WP11: a session the server ended because the plan no longer includes the desktop Engineer
    // says so on the login page, instead of the generic "your session expired".
    testWidgets('a session ended for the plan says why, not just "expired"', (tester) async {
      await openLogin(tester, appBaseUrl: '', extra: [
        sessionExpiredProvider.overrideWith(() => _Fixed(true)),
        sessionEndedForPlanProvider.overrideWith(() => _Fixed(true)),
      ]);
      expect(find.text(en.loginSessionEndedPlanRequired), findsOneWidget);
      expect(find.text(en.loginSessionExpired), findsNothing);
    });

    testWidgets('an ordinary expiry keeps the ordinary message', (tester) async {
      await openLogin(tester, appBaseUrl: '', extra: [
        sessionExpiredProvider.overrideWith(() => _Fixed(true)),
      ]);
      expect(find.text(en.loginSessionExpired), findsOneWidget);
      expect(find.text(en.loginSessionEndedPlanRequired), findsNothing);
    });

    testWidgets('Linux shows it next to the Google browser sign-in and starts the hand-off',
        (tester) async {
      debugDefaultTargetPlatformOverride = TargetPlatform.linux;
      try {
        final auth = await openLogin(tester, appBaseUrl: 'https://app.example.test');
        expect(find.widgetWithText(AuraButton, en.loginGoogleBrowser), findsOneWidget);
        await _press(tester, en.loginBrowserHandoff);
        expect(auth.calls, ['desktop-browser']);
      } finally {
        debugDefaultTargetPlatformOverride = null;
      }
    });

    testWidgets('Windows without APP_BASE_URL keeps only the Google browser sign-in',
        (tester) async {
      debugDefaultTargetPlatformOverride = TargetPlatform.windows;
      try {
        await openLogin(tester, appBaseUrl: '');
        expect(find.widgetWithText(AuraButton, en.loginGoogleBrowser), findsOneWidget);
        expect(find.byKey(const Key('login-desktop-browser')), findsNothing);
      } finally {
        debugDefaultTargetPlatformOverride = null;
      }
    });

    testWidgets('phones never show it', (tester) async {
      await openLogin(tester, appBaseUrl: 'https://app.example.test');
      expect(find.byKey(const Key('login-desktop-browser')), findsNothing);
    });
  });

  group('AuthNotifier.signInWithDesktopBrowser', () {
    late HttpServer backend;
    late List<Map<String, dynamic>> exchanges;
    late int status;
    late Object answer;
    late _MemoryRefreshTokenStore store;
    late List<String> identityLog;
    late List<Uri> opened;
    HttpOverrides? widgetTestOverrides;

    setUp(() async {
      // The widget tests above start Flutter's test binding, which answers every dart:io
      // HttpClient request with a canned 400 without sending it. These tests need the real
      // loopback round trip, so they lift that for their own duration.
      widgetTestOverrides = HttpOverrides.current;
      HttpOverrides.global = null;
      FlutterSecureStorage.setMockInitialValues({});
      exchanges = [];
      identityLog = [];
      opened = [];
      store = _MemoryRefreshTokenStore();
      status = 200;
      answer = {
        'access_token': _jwt({
          'sub': 'firebase-ann',
          'email': 'ann@example.com',
          'name': null,
          'picture': null,
          'role': 'User',
        }),
        'refresh_token': 'desktop-refresh',
        'token_type': 'bearer',
        'expires_in': 900,
        'device_id': 'dev-1',
      };
      backend = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      backend.listen((request) async {
        final raw = await utf8.decoder.bind(request).join();
        if (request.uri.path == '/api/v3/auth/desktop/firebase-exchange') {
          exchanges.add(jsonDecode(raw) as Map<String, dynamic>);
        }
        request.response
          ..statusCode = status
          ..headers.contentType = ContentType.json
          ..headers.set('X-Aura-Refusal', status == 403 ? 'plan_required' : 'none')
          ..write(jsonEncode(answer));
        await request.response.close();
      });
    });

    tearDown(() async {
      await backend.close(force: true);
      HttpOverrides.global = widgetTestOverrides;
    });

    ProviderContainer container({String appBaseUrl = 'https://app.example.test'}) {
      final c = ProviderContainer(overrides: [
        backendUrlProvider.overrideWith(() => _Backend('http://127.0.0.1:${backend.port}')),
        appBaseUrlProvider.overrideWithValue(appBaseUrl),
        identityServiceProvider.overrideWithValue(_FakeIdentity(identityLog)),
        tokenManagerProvider.overrideWith((ref) => TokenManager(
              ref,
              httpClient: MockClient((r) async => fail('unexpected ${r.method} ${r.url}')),
              store: store,
            )),
        // The real hand-off; only the browser is played here: it lands on the loopback listener
        // with a code and the state the app made, as the web page would.
        desktopBrowserHandoffProvider.overrideWithValue(({
          required Uri signInPage,
          required Uri exchangeEndpoint,
          String? deviceName,
        }) =>
            signInWithBrowserHandoff(
              signInPage: signInPage,
              exchangeEndpoint: exchangeEndpoint,
              deviceName: deviceName,
              launch: (page) {
                opened.add(page);
                final back = Uri.parse(page.queryParameters['redirect_uri']!).replace(
                  queryParameters: {'code': 'one-time', 'state': page.queryParameters['state']!},
                );
                unawaited(() async {
                  final client = HttpClient();
                  try {
                    await (await (await client.getUrl(back)).close()).drain<void>();
                  } finally {
                    client.close(force: true);
                  }
                }());
                return Future.value(true);
              },
            )),
      ]);
      addTearDown(c.dispose);
      return c;
    }

    test('opens the web page, redeems the code, and signs in as the session\'s account', () async {
      final c = container();
      await c.read(authProvider.future);

      await c.read(authProvider.notifier).signInWithDesktopBrowser();

      final profile = c.read(authProvider).value!;
      expect(profile.id, 'firebase-ann');
      expect(profile.email, 'ann@example.com');
      expect(profile.displayName, 'ann', reason: 'no name claim: the address stands in');
      expect(opened.single.toString(), startsWith('https://app.example.test/desktop-sign-in?'));
      expect(exchanges.single['code'], 'one-time');
      expect(exchanges.single['redirect_uri'], opened.single.queryParameters['redirect_uri']);
      expect(store.token, 'desktop-refresh');
      expect(await const FlutterSecureStorage().read(key: 'user_id'), 'firebase-ann');
      expect(identityLog, isEmpty, reason: 'the desktop app never signs in with Firebase itself');
    });

    test('a 403 (no desktop plan) is a refusal carrying the server\'s reason', () async {
      status = 403;
      answer = {'detail': 'The desktop Engineer needs a Pro or Team plan.'};
      final c = container();
      await c.read(authProvider.future);

      await c.read(authProvider.notifier).signInWithDesktopBrowser();

      final error = c.read(authProvider).error;
      expect(error, isA<SignInRefused>());
      expect((error! as SignInRefused).detail, 'The desktop Engineer needs a Pro or Team plan.');
      // WP11: the server's plan_required marker reaches the refusal, so the app can say why.
      expect((error as SignInRefused).planRequired, isTrue);
      expect(store.token, isNull);
    });

    test('a spent code is a failed sign-in to retry, not a refusal', () async {
      status = 400;
      answer = {'detail': 'Sign-in code is invalid, expired, or already used. Sign in again.'};
      final c = container();
      await c.read(authProvider.future);

      await c.read(authProvider.notifier).signInWithDesktopBrowser();

      expect(c.read(authProvider).error, isA<DesktopOAuthCancelledException>());
    });

    test('a build that does not know the web app opens nothing', () async {
      final c = container(appBaseUrl: '');
      await c.read(authProvider.future);

      await c.read(authProvider.notifier).signInWithDesktopBrowser();

      expect(c.read(authProvider).hasError, isTrue);
      expect(opened, isEmpty);
    });
  });
}


class _Fixed extends SessionExpiredNotifier {
  _Fixed(this.value);

  final bool value;

  @override
  bool build() => value;
}
