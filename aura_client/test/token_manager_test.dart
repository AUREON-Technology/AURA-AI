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
}
