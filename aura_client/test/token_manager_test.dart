import 'dart:convert';

import 'package:aura_client/core/auth/refresh_token_store.dart';
import 'package:aura_client/core/auth/token_manager.dart';
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

void main() {
  test('parallel getValidAccessToken calls share a single /refresh', () async {
    final store = _MemoryRefreshTokenStore('refresh-abc');
    var refreshCalls = 0;
    final httpClient = MockClient((request) async {
      if (request.url.path.endsWith('/refresh')) {
        refreshCalls++;
        expect(jsonDecode(request.body)['refresh_token'], 'refresh-abc');
        await Future<void>.delayed(const Duration(milliseconds: 80));
        return http.Response(
          jsonEncode({
            'access_token': 'access-1',
            'refresh_token': 'refresh-xyz',
            'expires_in': 900,
          }),
          200,
        );
      }
      fail('unexpected ${request.method} ${request.url}');
    });

    final container = ProviderContainer(
      overrides: [
        tokenManagerProvider.overrideWith(
          (ref) => TokenManager(ref, httpClient: httpClient, store: store),
        ),
      ],
    );
    addTearDown(container.dispose);

    final tm = container.read(tokenManagerProvider);
    final results = await Future.wait([
      tm.getValidAccessToken(),
      tm.getValidAccessToken(),
    ]);

    expect(results, ['access-1', 'access-1']);
    expect(refreshCalls, 1);
    expect(store.token, 'refresh-xyz');
  });

  test('invalidateAccessToken does not clear the refresh token', () async {
    final store = _MemoryRefreshTokenStore('refresh-abc');
    var refreshCalls = 0;
    final httpClient = MockClient((request) async {
      if (request.url.path.endsWith('/refresh')) {
        refreshCalls++;
        return http.Response(
          jsonEncode({
            'access_token': 'access-$refreshCalls',
            'refresh_token': 'refresh-kept',
            'expires_in': 900,
          }),
          200,
        );
      }
      fail('unexpected ${request.method} ${request.url}');
    });

    final container = ProviderContainer(
      overrides: [
        tokenManagerProvider.overrideWith(
          (ref) => TokenManager(ref, httpClient: httpClient, store: store),
        ),
      ],
    );
    addTearDown(container.dispose);

    final tm = container.read(tokenManagerProvider);
    expect(await tm.getValidAccessToken(), 'access-1');
    expect(store.token, 'refresh-kept');

    tm.invalidateAccessToken();
    expect(store.token, 'refresh-kept');
    expect(await tm.getValidAccessToken(), 'access-2');
    expect(refreshCalls, 2);
  });

  group('TokenManager.exchangeDesktopLogin', () {
    test('succeeds, caches the access token, and returns no refresh token', () async {
      final httpClient = MockClient((request) async {
        if (request.url.path.endsWith('/desktop/login')) {
          expect(request.headers['Authorization'], 'Bearer google-id-token');
          return http.Response(
            jsonEncode({
              'access_token': 'device-access-1',
              'token_type': 'bearer',
              'expires_in': 2592000,
              'device_id': 'dev-1',
            }),
            200,
          );
        }
        fail('unexpected ${request.method} ${request.url}');
      });

      final container = ProviderContainer(
        overrides: [
          tokenManagerProvider.overrideWith(
            (ref) => TokenManager(ref, httpClient: httpClient, store: _MemoryRefreshTokenStore()),
          ),
        ],
      );
      addTearDown(container.dispose);

      final tm = container.read(tokenManagerProvider);
      final token = await tm.exchangeDesktopLogin('google-id-token');

      expect(token, isNotNull);
      expect(token!.accessToken, 'device-access-1');
      expect(token.expiry.isAfter(DateTime.now().add(const Duration(days: 29))), isTrue);

      // getValidAccessToken must reuse this cached token rather than hitting /refresh or
      // /token, since exchangeDesktopLogin already populated TokenManager's in-memory state.
      expect(await tm.getValidAccessToken(), 'device-access-1');
    });

    test('returns null and does not cache anything on a non-200 response', () async {
      final httpClient = MockClient((request) async {
        return http.Response('{"detail":"Invalid or expired credentials."}', 401);
      });

      final container = ProviderContainer(
        overrides: [
          tokenManagerProvider.overrideWith(
            (ref) => TokenManager(ref, httpClient: httpClient, store: _MemoryRefreshTokenStore()),
          ),
        ],
      );
      addTearDown(container.dispose);

      final tm = container.read(tokenManagerProvider);
      expect(await tm.exchangeDesktopLogin('bad-token'), isNull);
      expect(
        container.read(authExchangeFailureProvider),
        AuthExchangeFailure.other,
      );
    });
  });

  test('applyPersistedDesktopToken makes getValidAccessToken reuse it without a network call', () async {
    final httpClient = MockClient((request) async {
      fail('unexpected ${request.method} ${request.url}');
    });

    final container = ProviderContainer(
      overrides: [
        tokenManagerProvider.overrideWith(
          (ref) => TokenManager(ref, httpClient: httpClient, store: _MemoryRefreshTokenStore()),
        ),
      ],
    );
    addTearDown(container.dispose);

    final tm = container.read(tokenManagerProvider);
    tm.applyPersistedDesktopToken(
      accessToken: 'restored-device-token',
      expiry: DateTime.now().add(const Duration(days: 10)),
    );

    expect(await tm.getValidAccessToken(), 'restored-device-token');
  });

  group('TokenManager.classifyExchangeFailure', () {
    test('a 403 matching the backend attestation error is classified distinctly', () {
      expect(
        TokenManager.classifyExchangeFailure(
          403,
          '{"detail":"Device attestation validation failed. (play_integrity_decode)"}',
        ),
        AuthExchangeFailure.deviceAttestationRejected,
      );
    });

    test('a 403 for an unrelated reason is not mistaken for attestation rejection', () {
      expect(
        TokenManager.classifyExchangeFailure(403, '{"detail":"Forbidden"}'),
        AuthExchangeFailure.other,
      );
    });

    test('a transient failure (network/500) is not mistaken for attestation rejection', () {
      expect(
        TokenManager.classifyExchangeFailure(500, 'internal server error'),
        AuthExchangeFailure.other,
      );
      expect(
        TokenManager.classifyExchangeFailure(0, ''),
        AuthExchangeFailure.other,
      );
    });
  });
}
