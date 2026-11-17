// WP8 (D-10, UX-06): the client half of `GET /api/v3/host/folders` -- whether the paired
// computer is online, whether remote control is on there, and which folders it has granted.
import 'dart:convert';

import 'package:aura_client/core/ai/engineer/host_folders_client.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late List<http.Request> requests;

  ProviderContainer containerWith(http.Response Function(http.Request) respond) {
    requests = [];
    final httpClient = MockClient((request) async {
      requests.add(request);
      return respond(request);
    });
    final container = ProviderContainer(overrides: [
      tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
      hostFoldersClientProvider.overrideWith((ref) => HostFoldersClient(ref, httpClient: httpClient)),
    ]);
    addTearDown(container.dispose);
    return container;
  }

  test('GETs the folders route with the JWT', () async {
    final container = containerWith(
        (_) => http.Response(jsonEncode({'connected': false, 'remote_control': null, 'roots': []}), 200));

    await container.read(hostFoldersClientProvider).fetch();

    expect(requests.single.method, 'GET');
    expect(requests.single.url.path, '/api/v3/host/folders');
    expect(requests.single.headers['Authorization'], 'Bearer jwt-test');
  });

  test('no computer online: not connected, remote control unknown, no folders', () async {
    final container = containerWith(
        (_) => http.Response(jsonEncode({'connected': false, 'remote_control': null, 'roots': []}), 200));

    final folders = await container.read(hostFoldersClientProvider).fetch();

    expect(folders.connected, isFalse);
    expect(folders.remoteControl, isNull);
    expect(folders.roots, isEmpty);
    expect(folders.state, HostFoldersState.notConnected);
  });

  test('online with remote control off: the folders are not offered', () async {
    final container = containerWith((_) => http.Response(
        jsonEncode({
          'connected': true,
          'remote_control': false,
          'roots': [
            {'path': '/home/me/repo', 'trusted': false},
          ],
        }),
        200));

    final folders = await container.read(hostFoldersClientProvider).fetch();

    expect(folders.connected, isTrue);
    expect(folders.remoteControl, isFalse);
    expect(folders.state, HostFoldersState.remoteControlOff);
  });

  test('online with remote control on: every granted folder, with its trust flag', () async {
    final container = containerWith((_) => http.Response(
        jsonEncode({
          'connected': true,
          'remote_control': true,
          'roots': [
            {'path': '/home/me/repo', 'trusted': true},
            {'path': r'C:\work\app', 'trusted': false},
          ],
        }),
        200));

    final folders = await container.read(hostFoldersClientProvider).fetch();

    expect(folders.state, HostFoldersState.available);
    expect(folders.roots.map((r) => r.path), ['/home/me/repo', r'C:\work\app']);
    expect(folders.roots.map((r) => r.trusted), [true, false]);
  });

  test('connected with remote control on but nothing granted has its own state', () async {
    final container = containerWith(
        (_) => http.Response(jsonEncode({'connected': true, 'remote_control': true, 'roots': []}), 200));

    final folders = await container.read(hostFoldersClientProvider).fetch();

    expect(folders.state, HostFoldersState.noFolders);
  });

  test('a 403 (no Engineer capability) surfaces as AuraApiError', () async {
    final container = containerWith((_) => http.Response(
        jsonEncode({'error': 'forbidden', 'message': 'engineer capability required', 'retryable': false}), 403));

    await expectLater(
      container.read(hostFoldersClientProvider).fetch(),
      throwsA(isA<AuraApiError>().having((e) => e.statusCode, 'statusCode', 403)),
    );
  });

  test('a body that is not the documented shape is a FormatException', () async {
    final container = containerWith((_) => http.Response(jsonEncode({'connected': 'yes'}), 200));

    await expectLater(container.read(hostFoldersClientProvider).fetch(), throwsFormatException);
  });

  test('the provider refetches when invalidated', () async {
    var connected = false;
    final container = containerWith((_) => http.Response(
        jsonEncode({
          'connected': connected,
          'remote_control': connected ? true : null,
          'roots': connected
              ? [
                  {'path': '/r', 'trusted': false},
                ]
              : [],
        }),
        200));
    final sub = container.listen(hostFoldersProvider, (_, _) {});
    addTearDown(sub.close);

    expect((await container.read(hostFoldersProvider.future)).connected, isFalse);
    connected = true;
    container.invalidate(hostFoldersProvider);
    expect((await container.read(hostFoldersProvider.future)).connected, isTrue);
    expect(requests, hasLength(2));
  });
}
