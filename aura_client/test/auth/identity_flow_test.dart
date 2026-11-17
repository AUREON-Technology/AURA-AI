/// SaaS P1-B: one Firebase identity layer for Google, Apple and email/password sign-in.
///
/// Every method ends in the same place: the Firebase ID token is exchanged for a backend session
/// (`TokenManager.startSession`), the previous account's local data goes if this is someone else,
/// and the profile is the Firebase uid -- which, after the subject-preserving import, is the same
/// id an existing Google user's data is stored under.
library;

import 'dart:convert';

import 'package:aura_client/core/auth/identity_service.dart';
import 'package:aura_client/core/auth/refresh_token_store.dart';
import 'package:aura_client/core/auth/sign_in_refused.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/config/backend_url_provider.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:drift/native.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _MemoryRefreshTokenStore implements RefreshTokenStore {
  String? token;
  @override
  Future<String?> read() async => token;
  @override
  Future<void> write(String value) async => token = value;
  @override
  Future<void> delete() async => token = null;
}

class _LocalBackend extends BackendUrlNotifier {
  @override
  String build() => 'http://127.0.0.1:8000';
}

class _CountingDb extends AppDatabase {
  _CountingDb() : super.forTesting(NativeDatabase.memory());
  int wipes = 0;
  @override
  Future<void> wipeUserData() async => wipes++;
}

class FakeIdentityService implements IdentityService {
  FakeIdentityService({this.user, this.error, this.token = 'firebase-id-token'});

  IdentityUser? user;
  IdentityError? error;
  String? token;
  final List<String> calls = [];

  Future<IdentityUser> _signIn(String how) async {
    calls.add(how);
    if (error != null) throw error!;
    return current = user!;
  }

  @override
  IdentityUser? current;

  @override
  Future<void> initialize() async {}

  @override
  Future<IdentityUser?> restore() async => current;

  @override
  Future<IdentityUser> signInWithGoogle() => _signIn('google');

  @override
  Future<IdentityUser> signInWithApple() => _signIn('apple');

  @override
  Future<IdentityUser> signInWithEmail(String email, String password) => _signIn('email:$email');

  @override
  Future<IdentityUser> registerWithEmail(String email, String password, {String? displayName}) =>
      _signIn('register:$email');

  @override
  Future<void> sendPasswordReset(String email) async => calls.add('reset:$email');

  @override
  Future<void> resendVerification() async => calls.add('resend');

  @override
  Future<String?> idToken({bool forceRefresh = false}) async => current == null ? null : token;

  @override
  Future<void> signOut() async {
    calls.add('signOut');
    current = null;
  }
}

const _ann = IdentityUser(
  uid: '109876543210987654321',
  email: 'ann@example.com',
  displayName: 'Ann',
  emailVerified: true,
  provider: 'password',
);

void main() {
  late List<http.Request> exchanges;
  late _CountingDb db;

  ProviderContainer container(FakeIdentityService identity, {int status = 200}) {
    exchanges = [];
    db = _CountingDb();
    final client = MockClient((request) async {
      exchanges.add(request);
      if (request.url.path.endsWith('/token')) {
        return status == 200
            ? http.Response(jsonEncode({'access_token': 'a', 'refresh_token': 'r', 'expires_in': 900}), 200)
            : http.Response('{"detail":"Invalid or expired credentials."}', status);
      }
      return http.Response('{}', 200);
    });
    final c = ProviderContainer(overrides: [
      backendUrlProvider.overrideWith(_LocalBackend.new),
      identityServiceProvider.overrideWithValue(identity),
      databaseProvider.overrideWith((ref) {
        ref.onDispose(db.close);
        return db;
      }),
      tokenManagerProvider.overrideWith(
        (ref) => TokenManager(ref, httpClient: client, store: _MemoryRefreshTokenStore()),
      ),
    ]);
    addTearDown(c.dispose);
    return c;
  }

  setUp(() => FlutterSecureStorage.setMockInitialValues({}));

  test('email sign-in starts the backend session with the Firebase token', () async {
    final identity = FakeIdentityService(user: _ann);
    final c = container(identity);
    await c.read(authProvider.future);

    await c.read(authProvider.notifier).signInWithEmail('ann@example.com', 'correct horse');

    expect(identity.calls, ['email:ann@example.com']);
    expect(exchanges.single.url.path, '/api/v3/auth/token');
    expect(exchanges.single.headers['Authorization'], 'Bearer firebase-id-token');
    final profile = c.read(authProvider).value!;
    expect(profile.id, _ann.uid);
    expect(profile.email, 'ann@example.com');
  });

  test('Apple sign-in goes through the same completion', () async {
    final identity = FakeIdentityService(user: _ann);
    final c = container(identity);
    await c.read(authProvider.future);

    await c.read(authProvider.notifier).signInWithApple();

    expect(identity.calls, ['apple']);
    expect(exchanges, hasLength(1));
    expect(c.read(authProvider).value?.id, _ann.uid);
  });

  test('a refused exchange signs out of the identity provider and reports a refusal', () async {
    final identity = FakeIdentityService(user: _ann);
    final c = container(identity, status: 401);
    await c.read(authProvider.future);

    await c.read(authProvider.notifier).signInWithGoogle();

    expect(identity.calls, ['google', 'signOut']);
    expect(c.read(authProvider).error, isA<SignInRefused>());
  });

  test('a cancelled sign-in is silent: no exchange, no error', () async {
    final identity = FakeIdentityService(
      error: const IdentityError(IdentityErrorKind.cancelled, 'closed'),
    );
    final c = container(identity);
    await c.read(authProvider.future);

    await c.read(authProvider.notifier).signInWithGoogle();

    expect(exchanges, isEmpty);
    expect(c.read(authProvider).hasError, isFalse);
    expect(c.read(authProvider).value, isNull);
  });

  test('a wrong password surfaces as an IdentityError the page can word', () async {
    final identity = FakeIdentityService(
      error: const IdentityError(IdentityErrorKind.wrongCredentials, 'bad'),
    );
    final c = container(identity);
    await c.read(authProvider.future);

    await c.read(authProvider.notifier).signInWithEmail('ann@example.com', 'nope');

    final error = c.read(authProvider).error;
    expect(error, isA<IdentityError>());
    expect((error! as IdentityError).kind, IdentityErrorKind.wrongCredentials);
    expect(exchanges, isEmpty);
  });

  test('switching to another account forgets the previous account\'s local data', () async {
    FlutterSecureStorage.setMockInitialValues({'user_id': 'someone-else'});
    final identity = FakeIdentityService(user: _ann);
    final c = container(identity);
    await c.read(authProvider.future);

    await c.read(authProvider.notifier).signInWithEmail('ann@example.com', 'correct horse');

    expect(db.wipes, 1);
  });

  test('the same account signing in again keeps its local data', () async {
    FlutterSecureStorage.setMockInitialValues({'user_id': _ann.uid});
    final identity = FakeIdentityService(user: _ann);
    final c = container(identity);
    await c.read(authProvider.future);

    await c.read(authProvider.notifier).signInWithEmail('ann@example.com', 'correct horse');

    expect(db.wipes, 0);
  });

  test('getIdToken asks the identity provider, not a cached Google token', () async {
    final identity = FakeIdentityService(user: _ann, token: 'fresh-firebase-token');
    final c = container(identity);
    await c.read(authProvider.future);
    await c.read(authProvider.notifier).signInWithEmail('ann@example.com', 'correct horse');

    expect(await c.read(authProvider.notifier).getIdToken(), 'fresh-firebase-token');
  });

  test('password reset and verification resend reach the identity provider', () async {
    final identity = FakeIdentityService(user: _ann);
    final c = container(identity);
    await c.read(authProvider.future);

    await c.read(authProvider.notifier).sendPasswordReset('ann@example.com');
    await c.read(authProvider.notifier).resendVerification();

    expect(identity.calls, ['reset:ann@example.com', 'resend']);
  });

  group('identityErrorKindFor', () {
    const table = {
      'wrong-password': IdentityErrorKind.wrongCredentials,
      'user-not-found': IdentityErrorKind.wrongCredentials,
      'invalid-credential': IdentityErrorKind.wrongCredentials,
      'invalid-login-credentials': IdentityErrorKind.wrongCredentials,
      'email-already-in-use': IdentityErrorKind.emailInUse,
      'weak-password': IdentityErrorKind.weakPassword,
      'invalid-email': IdentityErrorKind.invalidEmail,
      'network-request-failed': IdentityErrorKind.network,
      'account-exists-with-different-credential': IdentityErrorKind.accountExistsWithDifferentMethod,
      'popup-closed-by-user': IdentityErrorKind.cancelled,
      'cancelled-popup-request': IdentityErrorKind.cancelled,
      'web-context-canceled': IdentityErrorKind.cancelled,
      'canceled': IdentityErrorKind.cancelled,
      'operation-not-allowed': IdentityErrorKind.unavailable,
      'something-new': IdentityErrorKind.unavailable,
    };
    for (final entry in table.entries) {
      test(entry.key, () => expect(identityErrorKindFor(entry.key), entry.value));
    }
  });

  group('TokenManager.exchangePathFor', () {
    test('mobile uses the attested route', () {
      expect(TokenManager.exchangePathFor(isWeb: false), '/token');
    });
    test('the web uses the customer route', () {
      expect(TokenManager.exchangePathFor(isWeb: true), '/consumer/token');
    });
  });

  test('on the web the exchange goes to the customer route without attestation', () async {
    final requests = <http.Request>[];
    final client = MockClient((request) async {
      requests.add(request);
      return http.Response(jsonEncode({'access_token': 'a', 'refresh_token': 'r', 'expires_in': 900}), 200);
    });
    final c = ProviderContainer(overrides: [
      backendUrlProvider.overrideWith(_LocalBackend.new),
      tokenManagerProvider.overrideWith(
        (ref) => TokenManager(ref, httpClient: client, store: _MemoryRefreshTokenStore(), isWeb: true),
      ),
    ]);
    addTearDown(c.dispose);

    expect(await c.read(tokenManagerProvider).startSession('firebase-web'), SessionStart.started);
    expect(requests.single.url.path, '/api/v3/auth/consumer/token');
    expect(requests.single.headers.keys.map((k) => k.toLowerCase()), isNot(contains('x-device-attestation')));
  });
}
