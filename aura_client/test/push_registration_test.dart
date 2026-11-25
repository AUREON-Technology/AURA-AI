// Push registration contract (ADR-060): the real AuraApiClient against a MockClient that answers
// like the gateway.

import 'dart:convert';

import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/core/notifications/push_registration.dart';
import 'package:aura_client/features/auth/presentation/auth_provider.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _Token extends TokenManager {
  _Token(super.ref);
  @override
  Future<String?> getValidAccessToken() async => 'jwt';
}

class _SignedIn extends AuthNotifier {
  @override
  Future<UserProfile?> build() async =>
      UserProfile(id: 'u', email: 'u@example.com', displayName: 'u');

  @override
  Future<String?> getIdToken() async => null;
}

void main() {
  test('register posts the token and reads whether the server can deliver; unregister deletes', () async {
    final requests = <http.Request>[];
    final client = MockClient((request) async {
      requests.add(request);
      if (request.method == 'POST') {
        return http.Response(jsonEncode({'registered': true, 'delivering': false}), 200);
      }
      return http.Response(jsonEncode({'removed': true}), 200);
    });
    final container = ProviderContainer(overrides: [
      tokenManagerProvider.overrideWith((ref) => _Token(ref)),
      authProvider.overrideWith(_SignedIn.new),
      auraApiClientProvider.overrideWith((ref) => AuraApiClient(ref, httpClient: client)),
    ]);
    addTearDown(container.dispose);
    final push = container.read(pushRegistrationClientProvider);

    final result = await push.register(deviceId: 'pixel 7', token: 'tok', platform: 'android');
    expect(result.registered, isTrue);
    expect(result.delivering, isFalse, reason: 'a token may wait for the operator to switch push on');
    expect(await push.unregister('pixel 7'), isTrue);

    expect(requests.first.url.path, '/api/v3/push/devices');
    expect(jsonDecode(requests.first.body), {'device_id': 'pixel 7', 'token': 'tok', 'platform': 'android'});
    expect(requests.last.method, 'DELETE');
    expect(requests.last.url.path, '/api/v3/push/devices/pixel%207');
  });
}
