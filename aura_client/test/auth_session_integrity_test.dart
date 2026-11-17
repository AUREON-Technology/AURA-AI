// Session-integrity defects across the auth boundary (integration audit 2026-11-17):
//
// * INT-010 -- an account deleted elsewhere answers `account_deleted`; the client wipes and
//   ends the session instead of silently re-exchanging into a recreated account.
// * INT-011 -- `/token` never gets a cached attestation: its server nonce is single-use.
// * INT-012 -- a `/refresh` that got no answer is retried with the same `retry_key`, so the
//   server can tell it from a replay instead of revoking the whole family.
// * INT-027 -- a refresh still in flight at sign-out cannot write a token back afterwards.

import 'dart:async';
import 'dart:convert';

import 'package:aura_client/core/auth/refresh_token_store.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/security/device_validation_service.dart';
import 'package:aura_client/core/security/hardware_attestation_client.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
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

/// Signed in, with a Google identity still available for a silent re-exchange -- exactly the
/// second phone INT-010 is about.
class _SignedInAuth extends AuthNotifier {
  int deletedAccountEnds = 0;

  @override
  Future<UserProfile?> build() async =>
      UserProfile(id: 'user-1', email: 'a@example.com', displayName: 'A');

  @override
  Future<String?> getIdToken() async => 'google-still-signed-in';

  @override
  Future<void> endDeletedAccount() async {
    deletedAccountEnds++;
    await ref.read(tokenManagerProvider).clear();
    state = const AsyncValue.data(null);
  }
}

class _FakeAttestationClient implements HardwareAttestationClient {
  final nonces = <String?>[];

  @override
  Future<String?> requestToken({String? nonce}) async {
    nonces.add(nonce);
    return 'attestation-for-$nonce';
  }
}

http.Response _issued(String access, String refresh) => http.Response(
      jsonEncode({'access_token': access, 'refresh_token': refresh, 'expires_in': 900}),
      200,
    );

const _accountDeleted =
    '{"detail":{"error":"account_deleted","message":"This account has been deleted.","retryable":false}}';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(DeviceValidationService.clearCache);

  late List<http.Request> requests;
  late _MemoryRefreshTokenStore store;

  (ProviderContainer, TokenManager) scope(
    Future<http.Response> Function(http.Request) backend, {
    TokenExchangeAttestation? attestation,
  }) {
    requests = [];
    final client = MockClient((request) {
      requests.add(request);
      return backend(request);
    });
    final container = ProviderContainer(overrides: [
      authProvider.overrideWith(_SignedInAuth.new),
      tokenManagerProvider.overrideWith((ref) => TokenManager(
            ref,
            httpClient: client,
            store: store,
            attestationHeaders: attestation ?? (_) async => {'X-Device-Attestation': 'a'},
          )),
    ]);
    addTearDown(container.dispose);
    return (container, container.read(tokenManagerProvider));
  }

  Iterable<String> paths() => requests.map((r) => r.url.path.substring(r.url.path.lastIndexOf('/')));

  group('INT-010: an account deleted elsewhere', () {
    test('a refresh answered account_deleted wipes the session and never calls /token', () async {
      store = _MemoryRefreshTokenStore('refresh-of-deleted-account');
      final (container, tm) = scope((request) async {
        if (request.url.path.endsWith('/auth/refresh')) return http.Response(_accountDeleted, 401);
        return _issued('access-recreated', 'refresh-recreated');
      });
      await container.read(authProvider.future);

      tm.invalidateAccessToken();
      expect(await tm.getValidAccessToken(), isNull);

      expect(paths(), isNot(contains('/token')),
          reason: 'a silent re-exchange is what recreated the deleted account');
      expect((container.read(authProvider.notifier) as _SignedInAuth).deletedAccountEnds, 1);
      expect(container.read(authProvider).value, isNull);
      expect(store.token, isNull);
    });

    test('a silent /token answered account_deleted ends the account the same way', () async {
      store = _MemoryRefreshTokenStore();
      final (container, tm) = scope((_) async => http.Response(_accountDeleted, 401));
      await container.read(authProvider.future);

      expect(await tm.getValidAccessToken(), isNull);

      expect(requests.single.headers[signInHeader], isNull,
          reason: 'only a deliberate sign-in may start a fresh account under the subject');
      expect((container.read(authProvider.notifier) as _SignedInAuth).deletedAccountEnds, 1);
      expect(container.read(authProvider).value, isNull);
    });

    test('an ordinary refused refresh still re-exchanges as before', () async {
      store = _MemoryRefreshTokenStore('refresh-dead');
      final (container, tm) = scope((request) async {
        if (request.url.path.endsWith('/auth/refresh')) {
          return http.Response('{"detail":"Refresh token is invalid, expired, or already used."}', 401);
        }
        return _issued('access-2', 'refresh-2');
      });
      await container.read(authProvider.future);

      expect(await tm.getValidAccessToken(), 'access-2');
      expect((container.read(authProvider.notifier) as _SignedInAuth).deletedAccountEnds, 0);
    });

    test('a deliberate sign-in says so', () async {
      store = _MemoryRefreshTokenStore();
      final (_, tm) = scope((_) async => _issued('access-1', 'refresh-1'));

      expect(await tm.startSession('google-new'), SessionStart.started);
      expect(requests.single.headers[signInHeader], 'interactive');
    });
  });

  group('INT-011: /token never replays a nonce', () {
    test('two sign-ins within the cache window send two different nonces', () async {
      var issuedNonces = 0;
      final nonceServer = MockClient((request) async {
        if (request.url.path.endsWith('/auth/nonce')) {
          issuedNonces++;
          return http.Response(jsonEncode({'nonce': 'server-nonce-$issuedNonces'}), 200);
        }
        return http.Response('not found', 404);
      });
      final sdk = _FakeAttestationClient();
      Future<Map<String, String>> attest(String _) => DeviceValidationService.getAttestationHeaders(
            debugMode: false,
            platform: 'android',
            client: sdk,
            httpClient: nonceServer,
            singleUse: true,
          );
      store = _MemoryRefreshTokenStore();
      final (_, tm) = scope((_) async => _issued('access', 'refresh'), attestation: attest);

      await tm.startSession('google-1');
      await tm.clear(revoke: false);
      await tm.startSession('google-2');

      final sent = requests
          .where((r) => r.url.path.endsWith('/auth/token'))
          .map((r) => r.headers['X-Device-Attestation-Nonce'])
          .toList();
      expect(sent, ['server-nonce-1', 'server-nonce-2']);
      expect(sdk.nonces, ['server-nonce-1', 'server-nonce-2']);
    });

    test('a single-use attestation is neither served from nor left in the cache', () async {
      var issuedNonces = 0;
      final nonceServer = MockClient((request) async {
        issuedNonces++;
        return http.Response(jsonEncode({'nonce': 'n-$issuedNonces'}), 200);
      });
      Future<DeviceAttestation> resolve({bool singleUse = false}) => DeviceValidationService.resolve(
            debugMode: false,
            platform: 'android',
            client: _FakeAttestationClient(),
            httpClient: nonceServer,
            singleUse: singleUse,
          );

      expect((await resolve()).nonce, 'n-1'); // an ordinary call, cached
      expect((await resolve(singleUse: true)).nonce, 'n-2');
      expect((await resolve()).nonce, 'n-3', reason: 'the cache was dropped, not refilled with n-2');
    });

    test('sign-out drops the cached attestation', () async {
      var issuedNonces = 0;
      final nonceServer = MockClient((request) async {
        issuedNonces++;
        return http.Response(jsonEncode({'nonce': 'n-$issuedNonces'}), 200);
      });
      Future<DeviceAttestation> resolve() => DeviceValidationService.resolve(
            debugMode: false,
            platform: 'android',
            client: _FakeAttestationClient(),
            httpClient: nonceServer,
          );
      store = _MemoryRefreshTokenStore();
      final (_, tm) = scope((_) async => http.Response('{}', 200));

      await resolve();
      await tm.clear();
      expect((await resolve()).nonce, 'n-2');
    });
  });

  group('INT-012: a refresh whose answer was lost', () {
    test('is retried with the same token and the same retry key', () async {
      store = _MemoryRefreshTokenStore('refresh-1');
      var calls = 0;
      final (_, tm) = scope((request) async {
        if (!request.url.path.endsWith('/auth/refresh')) throw http.ClientException('offline');
        calls++;
        if (calls == 1) throw http.ClientException('timed out'); // rotated server-side; answer lost
        return _issued('access-2', 'refresh-3');
      });

      expect(await tm.getValidAccessToken(), isNull);
      expect(store.token, 'refresh-1', reason: 'no answer is not a refusal');
      expect(await tm.getValidAccessToken(), 'access-2');

      final bodies = requests
          .where((r) => r.url.path.endsWith('/auth/refresh'))
          .map((r) => jsonDecode(r.body) as Map<String, dynamic>)
          .toList();
      expect(bodies[0]['refresh_token'], 'refresh-1');
      expect(bodies[1]['refresh_token'], 'refresh-1');
      expect(bodies[0]['retry_key'], isNotEmpty);
      expect(bodies[1]['retry_key'], bodies[0]['retry_key']);
    });

    test('a new refresh after an answered one uses a new key', () async {
      store = _MemoryRefreshTokenStore('refresh-1');
      var n = 0;
      final (_, tm) = scope((_) async {
        n++;
        return http.Response(
          jsonEncode({'access_token': 'access-$n', 'refresh_token': 'refresh-${n + 1}', 'expires_in': 1}),
          200,
        );
      });

      await tm.getValidAccessToken();
      await tm.getValidAccessToken(); // expires_in 1 s: inside the freshness margin, so renewed
      final keys = requests.map((r) => (jsonDecode(r.body) as Map)['retry_key']).toList();
      expect(keys, hasLength(2));
      expect(keys[0], isNot(keys[1]));
    });
  });

  group('INT-027: sign-out during a refresh', () {
    test('the refresh that lands afterwards writes nothing and its token is revoked', () async {
      store = _MemoryRefreshTokenStore('refresh-1');
      final answer = Completer<http.Response>();
      final (_, tm) = scope((request) {
        if (request.url.path.endsWith('/auth/refresh')) return answer.future;
        return Future.value(http.Response('{"status":"revoked"}', 200));
      });

      final renewal = tm.getValidAccessToken();
      await Future<void>.delayed(Duration.zero);
      await tm.clear(revoke: false); // the user signs out
      answer.complete(_issued('access-late', 'refresh-late'));

      expect(await renewal, isNull);
      expect(store.token, isNull, reason: 'the signed-out session must not come back');
      expect(await tm.hasCachedRefreshToken(), isFalse);
      final revoked = requests
          .where((r) => r.url.path.endsWith('/auth/revoke'))
          .map((r) => (jsonDecode(r.body) as Map)['refresh_token']);
      expect(revoked, ['refresh-late']);
    });

    test('a new sign-in is not overwritten by the old session\'s late refresh', () async {
      store = _MemoryRefreshTokenStore('refresh-old');
      final answer = Completer<http.Response>();
      final (_, tm) = scope((request) {
        final path = request.url.path;
        if (path.endsWith('/auth/refresh')) return answer.future;
        if (path.endsWith('/auth/token')) return Future.value(_issued('access-new', 'refresh-new'));
        return Future.value(http.Response('{"status":"revoked"}', 200));
      });

      final renewal = tm.getValidAccessToken();
      await Future<void>.delayed(Duration.zero);
      expect(await tm.startSession('google-new'), SessionStart.started);
      answer.complete(_issued('access-old-late', 'refresh-old-late'));
      await renewal;

      expect(store.token, 'refresh-new');
      expect(await tm.getValidAccessToken(), 'access-new');
    });
  });
}
