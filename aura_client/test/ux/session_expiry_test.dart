/// Audit F-33: an expired backend session used to leave the user "signed in"
/// on a dead session, with every screen saying "Sign in again" and nothing to
/// press. A 401 followed by a refused renewal now ends the session, and the
/// router takes the user to the login page.
library;

import 'dart:convert';

import 'package:aura_client/core/auth/refresh_token_store.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/core/routing/router.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

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

/// Signed in, with no Google account to re-exchange with -- so once the
/// refresh token is refused there is nothing left to renew the session from.
class _SignedInAuth extends AuthNotifier {
  @override
  Future<UserProfile?> build() async =>
      UserProfile(id: 'user-1', email: 'a@example.com', displayName: 'A');

  @override
  Future<String?> getIdToken() async => null;
}

/// The backend, for these tests: every API call needs a token it accepts,
/// and `/refresh` answers with [refresh].
MockClient _backend(Future<http.Response> Function() refresh) {
  return MockClient((request) async {
    if (request.url.path.endsWith('/auth/refresh')) return refresh();
    if (request.headers['Authorization'] == 'Bearer access-good') {
      return http.Response('{"ok":true}', 200);
    }
    return http.Response('{"error":"authentication_error","message":"Invalid access token"}', 401);
  });
}

ProviderContainer _container(MockClient backend, _MemoryRefreshTokenStore store) {
  final container = ProviderContainer(
    overrides: [
      authProvider.overrideWith(_SignedInAuth.new),
      tokenManagerProvider.overrideWith(
        (ref) => TokenManager(ref, httpClient: backend, store: store),
      ),
      auraApiClientProvider.overrideWith((ref) => AuraApiClient(ref, httpClient: backend)),
    ],
  );
  addTearDown(container.dispose);
  return container;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    FlutterSecureStorage.setMockInitialValues({
      'user_id': 'user-1',
      'auth_token': 'stale-google-token',
    });
  });

  test('a 401 after a refused refresh signs the user out', () async {
    final store = _MemoryRefreshTokenStore('refresh-dead');
    final container = _container(
      _backend(() async => http.Response('{"detail":"Invalid refresh token"}', 401)),
      store,
    );
    expect((await container.read(authProvider.future))?.id, 'user-1');

    final response = await container.read(auraApiClientProvider).get('/api/v3/anything');

    expect(response.statusCode, 401);
    expect(container.read(authProvider).value, isNull, reason: 'the dead session must end');
    expect(container.read(sessionExpiredProvider), isTrue);
    expect(store.token, isNull);
    // The credential signInCached could otherwise revive the session from.
    expect(await const FlutterSecureStorage().read(key: 'auth_token'), isNull);
    // Kept, so a different account signing in next still gets a clean device.
    expect(await const FlutterSecureStorage().read(key: 'user_id'), 'user-1');
  });

  test('a refresh that fails on the network does not sign the user out', () async {
    final container = _container(
      _backend(() async => throw http.ClientException('connection reset')),
      _MemoryRefreshTokenStore('refresh-ok'),
    );
    await container.read(authProvider.future);

    await container.read(auraApiClientProvider).get('/api/v3/anything');

    expect(container.read(authProvider).value, isNotNull,
        reason: 'offline is not signed out');
  });

  test('a refresh the server cannot answer (503) does not sign the user out', () async {
    final container = _container(
      _backend(() async => http.Response('upstream unavailable', 503)),
      _MemoryRefreshTokenStore('refresh-ok'),
    );
    await container.read(authProvider.future);

    await container.read(auraApiClientProvider).get('/api/v3/anything');

    expect(container.read(authProvider).value, isNotNull);
  });

  test('a 401 the refresh recovers from keeps the session', () async {
    // The first token is one the API then rejects; the refresh after that
    // 401 issues one it accepts.
    var refreshes = 0;
    final container = _container(
      _backend(() async {
        refreshes++;
        return http.Response(
          jsonEncode({
            'access_token': refreshes == 1 ? 'access-revoked' : 'access-good',
            'refresh_token': 'refresh-$refreshes',
            'expires_in': 900,
          }),
          200,
        );
      }),
      _MemoryRefreshTokenStore('refresh-ok'),
    );
    await container.read(authProvider.future);

    final response = await container.read(auraApiClientProvider).get('/api/v3/anything');

    expect(response.statusCode, 200);
    expect(refreshes, 2);
    expect(container.read(authProvider).value, isNotNull);
  });

  testWidgets('the expired session ends on the login route', (tester) async {
    final store = _MemoryRefreshTokenStore('refresh-dead');
    final backend = _backend(() async => http.Response('{"detail":"Invalid refresh token"}', 401));
    late ProviderContainer container;

    // Same redirect rules as the app's router, over stub pages: the real
    // pages pull in the database, sync and Google sign-in.
    final testRouterProvider = Provider<GoRouter>((ref) {
      final notifier = RouterTransitionNotifier(ref);
      return GoRouter(
        initialLocation: '/today',
        refreshListenable: notifier,
        redirect: (context, state) => resolveAppRedirect(
          isLoggedIn: ref.read(authProvider).value != null,
          onboarded: true,
          location: state.matchedLocation,
          prefersDesktopIde: () async => false,
        ),
        routes: [
          GoRoute(path: '/login', builder: (_, _) => const Text('Login page')),
          GoRoute(path: '/today', builder: (_, _) => const Text('Today page')),
        ],
      );
    });

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          authProvider.overrideWith(_SignedInAuth.new),
          tokenManagerProvider.overrideWith(
            (ref) => TokenManager(ref, httpClient: backend, store: store),
          ),
          auraApiClientProvider.overrideWith((ref) => AuraApiClient(ref, httpClient: backend)),
        ],
        child: Consumer(
          builder: (context, ref, _) {
            container = ProviderScope.containerOf(context);
            return MaterialApp.router(routerConfig: ref.watch(testRouterProvider));
          },
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Today page'), findsOneWidget);

    await tester.runAsync(() => container.read(auraApiClientProvider).get('/api/v3/anything'));
    await tester.pumpAndSettle();

    expect(find.text('Today page'), findsNothing);
    expect(find.text('Login page'), findsOneWidget);
  });
}
