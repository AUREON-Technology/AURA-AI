// Exercises ControlServer end-to-end over real HTTP against a real local server -- no mocking of
// dart:io -- covering auth-token enforcement, root grant/revoke/trust, containment on the
// list/read browser endpoints, and the pairing flow against a local fake backend server (same
// pattern pairing_client_test.dart uses).
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:aura_hostd/src/config.dart';
import 'package:aura_hostd/src/control_server.dart';
import 'package:aura_hostd/src/workspace_store.dart';
import 'package:test/test.dart';

class _FakeAuthServer {
  _FakeAuthServer(this._httpServer);

  final HttpServer _httpServer;

  /// [pendingPolls]: how many `/pair/poll` calls report `"pending"` before the next one reports
  /// `"approved"` -- lets a test exercise the "still waiting" state deterministically instead of
  /// racing a real clock.
  static Future<_FakeAuthServer> start({
    required String userCode,
    required String deviceId,
    int pendingPolls = 0,
  }) async {
    final httpServer = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    final server = _FakeAuthServer(httpServer);
    var pollsSeen = 0;
    unawaited(() async {
      await for (final request in httpServer) {
        if (request.uri.path.endsWith('/pair/start')) {
          await _respond(request, {
            'pairing_id': 'pid-1',
            'user_code': userCode,
            'expires_in': 60,
            'interval': 0,
          });
        } else if (request.uri.path.endsWith('/pair/poll')) {
          if (pollsSeen < pendingPolls) {
            pollsSeen++;
            await _respond(request, {'status': 'pending'});
          } else {
            await _respond(request, {
              'status': 'approved',
              'access_token': 'tok-abc',
              'device_id': deviceId,
            });
          }
        } else {
          request.response.statusCode = 404;
          await request.response.close();
        }
      }
    }());
    return server;
  }

  static Future<void> _respond(HttpRequest request, Map<String, Object?> body) async {
    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode(body));
    await request.response.close();
  }

  String get backendUrl => 'http://127.0.0.1:${_httpServer.port}';

  Future<void> close() => _httpServer.close(force: true);
}

class _ControlClient {
  _ControlClient(this.port, this.token);

  final int port;
  final String token;
  final HttpClient _client = HttpClient();

  Future<void> close() async => _client.close(force: true);

  Future<(int, Map<String, dynamic>)> request(
    String method,
    String path, {
    Map<String, Object?>? body,
    bool withToken = true,
  }) async {
    final request = await _client.openUrl(method, Uri.parse('http://127.0.0.1:$port$path'));
    if (withToken) request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $token');
    if (body != null) {
      request.headers.contentType = ContentType.json;
      request.write(jsonEncode(body));
    }
    final response = await request.close();
    final raw = await utf8.decoder.bind(response).join();
    final decoded = raw.isEmpty ? <String, dynamic>{} : jsonDecode(raw) as Map<String, dynamic>;
    return (response.statusCode, decoded);
  }
}

void main() {
  late Directory configDir;
  late Directory workspaceDir;
  // The canonical (symlink-resolved) form of workspaceDir.path -- addRoot resolves symlinks
  // (containment.dart's discipline), which on some platforms (macOS's /tmp -> /private/tmp)
  // differs from the raw temp-dir path, so every request below addresses the root by this value,
  // not workspaceDir.path directly.
  late String workspaceRoot;
  late WorkspaceStore workspaceStore;
  late ConfigStore configStore;
  late ControlServer server;
  late _ControlClient client;

  setUp(() async {
    configDir = Directory.systemTemp.createTempSync('aura_hostd_control_test_config_');
    workspaceDir = Directory.systemTemp.createTempSync('aura_hostd_control_test_ws_');
    File('${workspaceDir.path}/hello.txt').writeAsStringSync('hello world');
    Directory('${workspaceDir.path}/subdir').createSync();
    workspaceStore = WorkspaceStore.forConfigDir(configDir);
    configStore = ConfigStore.forConfigDir(configDir);
    final roots = workspaceStore.load();
    workspaceRoot = roots.addRoot(workspaceDir.path, trusted: false).canonicalPath;
    workspaceStore.save(roots);

    server = ControlServer(
      workspaceStore: workspaceStore,
      configStore: configStore,
      onPaired: (_) {},
      logger: (_) {},
    );
    await server.start(configDir);
    final controlFile = jsonDecode(File('${configDir.path}/control.json').readAsStringSync())
        as Map<String, dynamic>;
    client = _ControlClient(controlFile['port'] as int, controlFile['token'] as String);
  });

  tearDown(() async {
    await client.close();
    await server.stop();
    configDir.deleteSync(recursive: true);
    workspaceDir.deleteSync(recursive: true);
  });

  test('GET /status requires no token and reports roots', () async {
    final (status, body) = await client.request('GET', '/status', withToken: false);
    expect(status, 200);
    expect(body['authenticated'], false);
    expect((body['roots'] as List).length, 1);
  });

  test('every other endpoint refuses a missing or wrong token', () async {
    final (missing, _) = await client.request('GET', '/roots', withToken: false);
    expect(missing, 401);

    final wrongToken = _ControlClient(client.port, 'not-the-real-token');
    final (wrong, _) = await wrongToken.request('GET', '/roots');
    expect(wrong, 401);
    await wrongToken.close();
  });

  test('grant, trust, and revoke a root round-trip through the same store the CLI uses', () async {
    final newRoot = Directory.systemTemp.createTempSync('aura_hostd_control_test_new_root_');
    addTearDown(() => newRoot.deleteSync(recursive: true));

    final (grantStatus, grantBody) =
        await client.request('POST', '/roots', body: {'action': 'grant', 'path': newRoot.path});
    expect(grantStatus, 200);
    final canonical = grantBody['root']['path'] as String;

    final (trustStatus, trustBody) =
        await client.request('POST', '/roots', body: {'action': 'trust', 'path': canonical});
    expect(trustStatus, 200);
    expect(trustBody['root']['trusted'], true);

    final (revokeStatus, revokeBody) =
        await client.request('POST', '/roots', body: {'action': 'revoke', 'path': canonical});
    expect(revokeStatus, 200);
    expect(revokeBody['removed'], true);

    final reloaded = workspaceStore.load();
    expect(reloaded.all.any((r) => r.canonicalPath == canonical), isFalse);
  });

  test('grant refuses a denylisted path the same way the CLI does', () async {
    final (status, body) = await client.request('POST', '/roots', body: {'action': 'grant', 'path': '/etc'});
    expect(status, 400);
    expect(body['error'], contains('denylist'));
  });

  test('GET /list returns entries inside the granted root', () async {
    final (status, body) = await client.request('GET', '/list?root=${Uri.encodeComponent(workspaceRoot)}');
    expect(status, 200);
    final names = (body['entries'] as List).map((e) => e['name']).toSet();
    expect(names, containsAll(['hello.txt', 'subdir']));
  });

  test('GET /list refuses a path that resolves outside the granted root', () async {
    final (status, body) = await client.request(
      'GET',
      '/list?root=${Uri.encodeComponent(workspaceRoot)}&path=${Uri.encodeComponent('../../etc')}',
    );
    expect(status, 403);
    expect(body['error'], contains('outside the granted workspace root'));
  });

  test('GET /read returns file content inside the granted root', () async {
    final (status, body) = await client.request(
      'GET',
      '/read?root=${Uri.encodeComponent(workspaceRoot)}&path=hello.txt',
    );
    expect(status, 200);
    expect(body['content'], 'hello world');
    expect(body['truncated'], false);
  });

  test('GET /read refuses a path outside the granted root', () async {
    final (status, _) = await client.request(
      'GET',
      '/read?root=${Uri.encodeComponent(workspaceRoot)}&path=${Uri.encodeComponent('../../etc/passwd')}',
    );
    expect(status, 403);
  });

  test('GET /read 404s a root that was never granted', () async {
    final (status, _) = await client.request('GET', '/read?root=/not/a/real/root&path=x');
    expect(status, 404);
  });

  test('POST /pair/start returns the code immediately, before any approval', () async {
    final fakeBackend = await _FakeAuthServer.start(
      userCode: 'AB3D-9XYZ',
      deviceId: 'dev-1',
      pendingPolls: 1000, // never approves within this test's lifetime
    );
    addTearDown(fakeBackend.close);

    final (status, body) =
        await client.request('POST', '/pair/start', body: {'backend_url': fakeBackend.backendUrl});
    expect(status, 200);
    expect(body['user_code'], 'AB3D-9XYZ');
    expect(body['expires_in'], isA<int>());
  });

  test('GET /pair/status without a prior /pair/start is a clear error, not a hang', () async {
    final (status, body) = await client.request('GET', '/pair/status');
    expect(status, 409);
    expect(body['error'], contains('/pair/start'));
  });

  test('GET /pair/status reports pending then approved, and calls onPaired on approval', () async {
    final fakeBackend = await _FakeAuthServer.start(userCode: 'AB3D-9XYZ', deviceId: 'dev-1', pendingPolls: 1);
    addTearDown(fakeBackend.close);

    String? paired;
    final pairingServer = ControlServer(
      workspaceStore: workspaceStore,
      configStore: configStore,
      onPaired: (token) => paired = token,
      logger: (_) {},
    );
    final pairingConfigDir = Directory.systemTemp.createTempSync('aura_hostd_control_test_pair_');
    addTearDown(() => pairingConfigDir.deleteSync(recursive: true));
    await pairingServer.start(pairingConfigDir);
    final controlFile =
        jsonDecode(File('${pairingConfigDir.path}/control.json').readAsStringSync()) as Map<String, dynamic>;
    final pairingClient = _ControlClient(controlFile['port'] as int, controlFile['token'] as String);
    addTearDown(pairingClient.close);

    final (startStatus, startBody) = await pairingClient.request(
      'POST',
      '/pair/start',
      body: {'backend_url': fakeBackend.backendUrl},
    );
    expect(startStatus, 200);
    expect(startBody['user_code'], 'AB3D-9XYZ');

    final (firstPollStatus, firstPollBody) = await pairingClient.request('GET', '/pair/status');
    expect(firstPollStatus, 200);
    expect(firstPollBody['status'], 'pending');
    expect(paired, isNull);

    final (secondPollStatus, secondPollBody) = await pairingClient.request('GET', '/pair/status');
    expect(secondPollStatus, 200);
    expect(secondPollBody['status'], 'approved');
    expect(secondPollBody['device_id'], 'dev-1');
    expect(paired, 'tok-abc');
    expect(ConfigStore.forConfigDir(pairingConfigDir).load().authToken, 'tok-abc');

    await pairingServer.stop();
  });
}
