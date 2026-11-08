import 'dart:convert';

import 'package:aura_client/core/auth/desktop_devices_client.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/settings/presentation/desktop_devices_page.dart';
import 'package:aura_client/l10n/generated/app_localizations.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

const _twoDevices = {
  'devices': [
    {
      'device_id': 'd-laptop',
      'name': 'anas-laptop',
      'kind': 'app',
      'created_at': '2026-11-01T10:00:00+00:00',
      'current': true,
    },
    {
      'device_id': 'd-build',
      'name': 'build-box',
      'kind': 'daemon',
      'created_at': '2026-10-20T09:00:00+00:00',
      'current': false,
    },
  ],
};

Widget _app(Widget home, http.Client httpClient) => ProviderScope(
      overrides: [
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        desktopDevicesClientProvider.overrideWith((ref) => DesktopDevicesClient(ref, httpClient: httpClient)),
      ],
      child: MaterialApp(
        theme: AuraThemeV2.light,
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: home,
      ),
    );

void main() {
  group('DesktopDevicesClient', () {
    test('list GETs /api/v3/auth/desktop/devices with the JWT and parses devices', () async {
      http.Request? recorded;
      final httpClient = MockClient((request) async {
        recorded = request;
        return http.Response(jsonEncode(_twoDevices), 200);
      });
      final container = ProviderContainer(overrides: [
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        desktopDevicesClientProvider.overrideWith((ref) => DesktopDevicesClient(ref, httpClient: httpClient)),
      ]);
      addTearDown(container.dispose);

      final devices = await container.read(desktopDevicesClientProvider).list();

      expect(recorded!.method, 'GET');
      expect(recorded!.url.path, '/api/v3/auth/desktop/devices');
      expect(recorded!.headers['Authorization'], 'Bearer jwt-test');
      expect(devices.map((d) => d.deviceId), ['d-laptop', 'd-build']);
      expect(devices.first.current, isTrue);
      expect(devices.first.kind, 'app');
      expect(devices.last.createdAt, isNotNull);
    });

    test('revoke POSTs the device id and throws on a non-200', () async {
      http.Request? recorded;
      var status = 200;
      final httpClient = MockClient((request) async {
        recorded = request;
        return http.Response(status == 200 ? '{"status":"revoked"}' : '{}', status);
      });
      final container = ProviderContainer(overrides: [
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        desktopDevicesClientProvider.overrideWith((ref) => DesktopDevicesClient(ref, httpClient: httpClient)),
      ]);
      addTearDown(container.dispose);

      await container.read(desktopDevicesClientProvider).revoke('d-build');
      expect(recorded!.method, 'POST');
      expect(recorded!.url.path, '/api/v3/auth/desktop/devices/revoke');
      expect(jsonDecode(recorded!.body), {'device_id': 'd-build'});

      status = 404;
      await expectLater(
        container.read(desktopDevicesClientProvider).revoke('nope'),
        throwsA(isA<http.ClientException>()),
      );
    });
  });

  group('DesktopDevicesPage', () {
    testWidgets('lists every device with its kind and marks the current one', (tester) async {
      final httpClient = MockClient((request) async => http.Response(jsonEncode(_twoDevices), 200));
      await tester.pumpWidget(_app(const DesktopDevicesPage(), httpClient));
      await tester.pumpAndSettle();

      expect(find.text('anas-laptop'), findsOneWidget);
      expect(find.text('build-box'), findsOneWidget);
      expect(find.textContaining('Desktop app \u00b7'), findsOneWidget);
      expect(find.textContaining('Engineer daemon \u00b7'), findsOneWidget);
      expect(find.textContaining('this device'), findsOneWidget);
      expect(find.byKey(const ValueKey('revoke-d-build')), findsOneWidget);
    });

    testWidgets('revoke asks first, then posts and reloads without the device', (tester) async {
      final calls = <String>[];
      var revoked = false;
      final httpClient = MockClient((request) async {
        calls.add('${request.method} ${request.url.path}');
        if (request.url.path.endsWith('/revoke')) {
          revoked = true;
          return http.Response('{"status":"revoked"}', 200);
        }
        final devices = (_twoDevices['devices'] as List)
            .where((d) => !revoked || (d as Map)['device_id'] != 'd-build')
            .toList();
        return http.Response(jsonEncode({'devices': devices}), 200);
      });
      await tester.pumpWidget(_app(const DesktopDevicesPage(), httpClient));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const ValueKey('revoke-d-build')));
      await tester.pumpAndSettle();
      expect(find.textContaining('build-box will be signed out'), findsOneWidget);
      expect(calls.where((c) => c.endsWith('/revoke')), isEmpty,
          reason: 'nothing is revoked before confirmation');

      await tester.tap(find.widgetWithText(AuraButton, 'Revoke'));
      await tester.pumpAndSettle();

      expect(calls.where((c) => c.endsWith('/revoke')).length, 1);
      expect(find.text('build-box'), findsNothing);
      expect(find.text('anas-laptop'), findsOneWidget);
    });

    testWidgets('an empty account shows the empty state', (tester) async {
      final httpClient = MockClient((request) async => http.Response('{"devices": []}', 200));
      await tester.pumpWidget(_app(const DesktopDevicesPage(), httpClient));
      await tester.pumpAndSettle();
      expect(find.text('No desktop devices'), findsOneWidget);
    });
  });
}
