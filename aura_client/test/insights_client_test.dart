import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/insights/insights_client.dart';
import 'package:aura_client/core/platform/device_timezone.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

/// The backend computes "today" for insights and the daily briefing in the zone this header
/// reports (audit F-17); without it a UTC+3 user is told "0ml of water logged today" every
/// evening.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  Future<List<http.Request>> callBoth(DeviceTimezoneLookup timezone) async {
    final recorded = <http.Request>[];
    final httpClient = MockClient((request) async {
      recorded.add(request);
      return http.Response('[]', 200);
    });
    final container = ProviderContainer(
      overrides: [
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        insightsClientProvider.overrideWith(
          (ref) => InsightsClient(ref, httpClient: httpClient, timezone: timezone),
        ),
      ],
    );
    addTearDown(container.dispose);
    final client = container.read(insightsClientProvider);
    await client.fetch();
    await client.generate();
    return recorded;
  }

  test('fetch and generate send the device IANA zone', () async {
    final requests = await callBoth(() async => 'Asia/Riyadh');

    expect(requests.map((r) => r.url.path), ['/api/v3/insights', '/api/v3/insights/generate']);
    for (final request in requests) {
      expect(request.headers[clientTimezoneHeader], 'Asia/Riyadh');
      expect(request.headers['Authorization'], 'Bearer jwt-test');
    }
  });

  test('an unknown zone sends no header rather than a guess', () async {
    final requests = await callBoth(() async => null);

    expect(requests, hasLength(2));
    for (final request in requests) {
      expect(request.headers.containsKey(clientTimezoneHeader), isFalse);
    }
  });

  test('deviceTimezone is null, not a throw, where the plugin is missing', () async {
    // No platform channel handler is registered under `flutter test`.
    expect(await deviceTimezone(), isNull);
  });
}
