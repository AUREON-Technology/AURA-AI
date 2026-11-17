import 'dart:convert';
import 'dart:ui' show Locale;

import 'package:aura_client/core/platform/client_language.dart';
import 'package:aura_client/core/security/device_validation_service.dart';
import 'package:aura_client/core/security/hardware_attestation_client.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

// Every request tells the backend which language the app is showing (X-Client-Language), so a
// reply to a message with no clear language of its own, and the daily briefing, come back in it
// (docs/plans/arabic-language_plan.md, stage 1).

class _FakeAttestationClient implements HardwareAttestationClient {
  @override
  Future<String?> requestToken({String? nonce}) async => 'header.payload.signature';
}

http.Client _fakeNonceClient() => MockClient((request) async {
      if (request.url.path.endsWith('/auth/nonce')) {
        return http.Response(jsonEncode({'nonce': 'n-1'}), 200);
      }
      return http.Response('not found', 404);
    });

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  tearDown(() {
    ClientLanguage.override = null;
    TestWidgetsFlutterBinding.instance.platformDispatcher.clearLocaleTestValue();
    DeviceValidationService.clearCache();
  });

  test('follows the device language, reduced to its primary subtag', () {
    TestWidgetsFlutterBinding.instance.platformDispatcher.localeTestValue = const Locale('ar', 'EG');
    expect(ClientLanguage.current(), 'ar');

    TestWidgetsFlutterBinding.instance.platformDispatcher.localeTestValue = const Locale('en', 'US');
    expect(ClientLanguage.current(), 'en');
  });

  test("the in-app choice wins over the device's language", () {
    TestWidgetsFlutterBinding.instance.platformDispatcher.localeTestValue = const Locale('en', 'US');
    ClientLanguage.override = () => 'ar';
    expect(ClientLanguage.current(), 'ar');

    // "System" is expressed as no override, or an empty one: back to the device.
    ClientLanguage.override = () => null;
    expect(ClientLanguage.current(), 'en');
    ClientLanguage.override = () => '  ';
    expect(ClientLanguage.current(), 'en');
  });

  test('rides the headers every API client sends, sign-in included', () async {
    TestWidgetsFlutterBinding.instance.platformDispatcher.localeTestValue = const Locale('ar', 'EG');
    final headers = await DeviceValidationService.getAttestationHeaders(
      debugMode: true,
      platform: 'android',
      backendUrl: 'https://aureon-ai.tech',
      client: _FakeAttestationClient(),
      httpClient: _fakeNonceClient(),
    );
    expect(headers[clientLanguageHeader], 'ar');
    expect(clientLanguageHeader, 'X-Client-Language');
  });
}
