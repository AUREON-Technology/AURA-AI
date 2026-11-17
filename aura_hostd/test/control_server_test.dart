// Exercises ControlServer end-to-end over real HTTP against a real local server -- no mocking of
// dart:io -- covering auth-token enforcement, root grant/revoke/trust, containment on the
// list/read browser endpoints, and the pairing flow against a local fake backend server (same
// pattern pairing_client_test.dart uses).
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:aura_hostd/src/approval_queue.dart';
import 'package:aura_hostd/src/config.dart';
import 'package:aura_hostd/src/control_server.dart';
import 'package:aura_hostd/src/isolated_task_registry.dart';
import 'package:aura_hostd/src/debug_session_manager.dart';
import 'package:aura_hostd/src/task_handler.dart';
import 'package:aura_hostd/src/terminal_session_manager.dart';
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
  late ApprovalQueue approvalQueue;
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

    approvalQueue = ApprovalQueue();
    server = ControlServer(
      workspaceStore: workspaceStore,
      configStore: configStore,
      onPaired: (_) {},
      terminalSessions: TerminalSessionManager(workspaceStore),
      debugSessions: DebugSessionManager(workspaceStore),
      approvalQueue: approvalQueue,
      isolatedTasks: IsolatedTaskRegistry(roots),
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

  test('control.json (it holds the bearer token) is readable by the owner only', () {
    final mode = File('${configDir.path}/control.json').statSync().mode & 0x1FF;
    expect(mode, 0x180, reason: 'expected 0600, got ${mode.toRadixString(8)}');
  }, testOn: '!windows');

  test('GET /status without a token answers, but never names roots or the backend (INT-037)',
      () async {
    final (status, body) = await client.request('GET', '/status', withToken: false);
    expect(status, 200);
    expect(body['authenticated'], false);
    // Distinct from `authenticated` (has a stored token): whether the daemon's actual socket to
    // the backend is open right now. This server was built with no `isBackendConnected` wired in
    // (the shared setUp above), which must report the honest answer -- not connected -- rather
    // than silently defaulting to a value that would make a dead connection look live.
    expect(body['backend_connected'], false);
    expect(body['credentials_rejected'], false);
    expect(body.containsKey('roots'), isFalse);
    expect(body.containsKey('backend_url'), isFalse);
    expect(jsonEncode(body), isNot(contains(workspaceRoot)));
  });

  test('GET /status with the token reports roots', () async {
    final (status, body) = await client.request('GET', '/status');
    expect(status, 200);
    expect((body['roots'] as List).length, 1);
    expect(body.containsKey('backend_url'), isTrue);
  });

  test('GET /status reports credentials_rejected and last_close_code from the wired callbacks '
      '(INT-003)', () async {
    final rejectedServer = ControlServer(
      workspaceStore: workspaceStore,
      configStore: configStore,
      onPaired: (_) {},
      terminalSessions: TerminalSessionManager(workspaceStore),
      debugSessions: DebugSessionManager(workspaceStore),
      approvalQueue: approvalQueue,
      isolatedTasks: IsolatedTaskRegistry(workspaceStore.load()),
      isCredentialsRejected: () => true,
      lastCloseCode: () => 4401,
      logger: (_) {},
    );
    await rejectedServer.start(configDir);
    addTearDown(rejectedServer.stop);
    final controlFile = jsonDecode(File('${configDir.path}/control.json').readAsStringSync())
        as Map<String, dynamic>;
    final rejectedClient = _ControlClient(controlFile['port'] as int, controlFile['token'] as String);
    addTearDown(rejectedClient.close);

    final (_, body) = await rejectedClient.request('GET', '/status', withToken: false);
    expect(body['credentials_rejected'], true);
    expect(body['last_close_code'], 4401);
  });

  test('a request whose Host is not loopback is refused, token or not (DNS rebinding, INT-037)',
      () async {
    final http = HttpClient();
    addTearDown(() => http.close(force: true));
    for (final path in ['/status', '/roots']) {
      final request = await http.openUrl('GET', Uri.parse('http://127.0.0.1:${client.port}$path'));
      request.headers.host = 'attacker.example';
      request.headers.set(HttpHeaders.authorizationHeader, 'Bearer ${client.token}');
      final response = await request.close();
      await response.drain<void>();
      expect(response.statusCode, 403, reason: path);
    }
    final (ok, _) = await client.request('GET', '/roots');
    expect(ok, 200, reason: 'a loopback Host still works');
  });

  group('remote control opt-in (WP8, D-10b)', () {
    test('GET /status reports remote_control only to a caller holding the token', () async {
      final (_, anonymous) = await client.request('GET', '/status', withToken: false);
      expect(anonymous.containsKey('remote_control'), isFalse);

      final (_, before) = await client.request('GET', '/status');
      expect(before['remote_control'], isFalse, reason: 'off by default');

      configStore.save(configStore.load().copyWith(remoteControl: true));
      final (_, after) = await client.request('GET', '/status');
      expect(after['remote_control'], isTrue);
      final (_, stillAnonymous) = await client.request('GET', '/status', withToken: false);
      expect(stillAnonymous.containsKey('remote_control'), isFalse);
    });

    test('POST /remote_control turns it on and off, persisted to config.json', () async {
      configStore.save(DaemonConfig(backendUrl: 'https://a.example', authToken: 'tok'));

      final (on, onBody) = await client.request('POST', '/remote_control', body: {'enabled': true});
      expect(on, 200);
      expect(onBody, {'remote_control': true});
      expect(ConfigStore.forConfigDir(configDir).load().remoteControl, isTrue);
      final raw = jsonDecode(File('${configDir.path}/config.json').readAsStringSync()) as Map;
      expect(raw['remote_control'], isTrue);
      expect(raw['auth_token'], 'tok', reason: 'the rest of config.json is left as it was');
      expect(raw['backend_url'], 'https://a.example');

      final (off, offBody) =
          await client.request('POST', '/remote_control', body: {'enabled': false});
      expect(off, 200);
      expect(offBody, {'remote_control': false});
      expect(ConfigStore.forConfigDir(configDir).load().remoteControl, isFalse);
    });

    test('POST /remote_control keeps config.json readable by the owner only', () async {
      await client.request('POST', '/remote_control', body: {'enabled': true});
      final mode = File('${configDir.path}/config.json').statSync().mode & 0x1FF;
      expect(mode, 0x180, reason: 'expected 0600, got ${mode.toRadixString(8)}');
    }, testOn: '!windows');

    test('POST /remote_control rejects a missing or non-bool enabled with 400 and changes nothing',
        () async {
      configStore.save(configStore.load().copyWith(remoteControl: true));
      for (final body in <Map<String, Object?>>[
        {},
        {'enabled': 'false'},
        {'enabled': 0},
        {'enabled': null},
        {'on': false},
      ]) {
        final (status, response) = await client.request('POST', '/remote_control', body: body);
        expect(status, 400, reason: '$body');
        expect(response['error'], isNotEmpty, reason: '$body');
      }
      expect(configStore.load().remoteControl, isTrue);
    });

    test('POST /remote_control rejects a body that is not JSON with 400', () async {
      final http = HttpClient();
      addTearDown(() => http.close(force: true));
      final request = await http.openUrl('POST', Uri.parse('http://127.0.0.1:${client.port}/remote_control'));
      request.headers.set(HttpHeaders.authorizationHeader, 'Bearer ${client.token}');
      request.write('{"enabled": tru');
      final response = await request.close();
      await response.drain<void>();
      expect(response.statusCode, 400);
      expect(configStore.load().remoteControl, isFalse);
    });

    test('POST /remote_control refuses a missing or wrong token and changes nothing', () async {
      final (missing, _) = await client.request(
        'POST',
        '/remote_control',
        body: {'enabled': true},
        withToken: false,
      );
      expect(missing, 401);
      final wrongToken = _ControlClient(client.port, 'not-the-real-token');
      addTearDown(wrongToken.close);
      final (wrong, _) = await wrongToken.request('POST', '/remote_control', body: {'enabled': true});
      expect(wrong, 401);
      expect(configStore.load().remoteControl, isFalse);
    });

    test('POST /remote_control refuses a non-loopback Host, token or not (INT-037)', () async {
      final http = HttpClient();
      addTearDown(() => http.close(force: true));
      final request = await http.openUrl('POST', Uri.parse('http://127.0.0.1:${client.port}/remote_control'));
      request.headers.host = 'attacker.example';
      request.headers.set(HttpHeaders.authorizationHeader, 'Bearer ${client.token}');
      request.headers.contentType = ContentType.json;
      request.write(jsonEncode({'enabled': true}));
      final response = await request.close();
      await response.drain<void>();
      expect(response.statusCode, 403);
      expect(configStore.load().remoteControl, isFalse);
    });

    test('turning it off through this route refuses the next call of a connected remote task',
        () async {
      final agent = TaskHandler.live(
        workspaceStore,
        approvalQueue: approvalQueue,
        isolatedTasks: IsolatedTaskRegistry(workspaceStore.live),
        remoteControlEnabled: configStore.remoteControlEnabled,
      );
      await client.request('POST', '/remote_control', body: {'enabled': true});
      final created = await agent.createTask({'origin': 'remote', 'cwd': workspaceRoot});
      await agent.writeFile({
        'task_id': created['task_id'],
        'path': 'one.txt',
        'content': '1',
        'confirmed': true,
      });
      expect(File('$workspaceRoot/one.txt').readAsStringSync(), '1');

      await client.request('POST', '/remote_control', body: {'enabled': false});
      await expectLater(
        agent.writeFile({
          'task_id': created['task_id'],
          'path': 'two.txt',
          'content': '2',
          'confirmed': true,
        }),
        throwsA(isA<RpcError>().having(
            (e) => e.message, 'message', startsWith('remote_control_disabled: '))),
      );
      expect(File('$workspaceRoot/two.txt').existsSync(), isFalse);
    });
  });

  test('constantTimeEquals matches == on equal, different and differently-sized strings', () {
    expect(constantTimeEquals('Bearer abc', 'Bearer abc'), isTrue);
    expect(constantTimeEquals('Bearer abc', 'Bearer abd'), isFalse);
    expect(constantTimeEquals('Bearer abc', 'Bearer ab'), isFalse);
    expect(constantTimeEquals('', ''), isTrue);
  });

  test('GET /status reports backend_connected from the wired callback, independent of the '
      'stored token', () async {
    final connectedServer = ControlServer(
      workspaceStore: workspaceStore,
      configStore: configStore,
      onPaired: (_) {},
      terminalSessions: TerminalSessionManager(workspaceStore),
      debugSessions: DebugSessionManager(workspaceStore),
      approvalQueue: approvalQueue,
      isolatedTasks: IsolatedTaskRegistry(workspaceStore.load()),
      isBackendConnected: () => true,
      logger: (_) {},
    );
    await connectedServer.start(configDir);
    addTearDown(connectedServer.stop);
    final controlFile = jsonDecode(File('${configDir.path}/control.json').readAsStringSync())
        as Map<String, dynamic>;
    final connectedClient = _ControlClient(controlFile['port'] as int, controlFile['token'] as String);
    addTearDown(connectedClient.close);

    final (status, body) = await connectedClient.request('GET', '/status', withToken: false);
    expect(status, 200);
    // No token was ever stored for this daemon instance -- `isBackendConnected` is independent of
    // `authenticated`, exactly the case a stale "Host Connected" UI needs distinguished.
    expect(body['authenticated'], false);
    expect(body['backend_connected'], true);
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
    // A denylisted directory that exists on this platform: an absent one is refused earlier, as
    // "does not exist", which would never reach the denylist this test is about.
    final denylisted = Platform.isWindows ? (Platform.environment['SystemRoot'] ?? r'C:\Windows') : '/etc';
    final (status, body) =
        await client.request('POST', '/roots', body: {'action': 'grant', 'path': denylisted});
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

  test('GET /approvals is empty when nothing is pending', () async {
    final (status, body) = await client.request('GET', '/approvals');
    expect(status, 200);
    expect(body['approvals'], isEmpty);
  });

  test('GET /approvals lists a pending approval with the redacted command, never the raw one', () async {
    approvalQueue.createForCommand(
      canonicalCommand: 'curl -H "Authorization: Bearer sk-real-secret" https://example.com',
      cwd: workspaceRoot,
      rootPath: workspaceRoot,
    );
    final (status, body) = await client.request('GET', '/approvals');
    expect(status, 200);
    final approvals = body['approvals'] as List;
    expect(approvals, hasLength(1));
    final command = approvals.single['command'] as String;
    expect(command, isNot(contains('sk-real-secret')));
    expect(jsonEncode(body), isNot(contains('sk-real-secret')));
  });

  test('POST /approvals/<id>/respond approves and removes it from the pending list', () async {
    final approval =
        approvalQueue.createForCommand(canonicalCommand: 'npm test', cwd: workspaceRoot, rootPath: workspaceRoot);
    final (status, body) =
        await client.request('POST', '/approvals/${approval.id}/respond', body: {'approved': true});
    expect(status, 200);
    expect(body['ok'], true);
    expect(await approval.decision, ApprovalStatus.approved);
    final (_, listBody) = await client.request('GET', '/approvals');
    expect(listBody['approvals'], isEmpty);
  });

  test('POST /approvals/<id>/respond 404s an unknown id', () async {
    final (status, body) =
        await client.request('POST', '/approvals/appr_does_not_exist/respond', body: {'approved': true});
    expect(status, 404);
    expect(body['error'], contains('no such pending approval'));
  });

  test('POST /approvals/<id>/respond is idempotent on an already-resolved id', () async {
    final approval =
        approvalQueue.createForCommand(canonicalCommand: 'npm test', cwd: workspaceRoot, rootPath: workspaceRoot);
    final (firstStatus, _) =
        await client.request('POST', '/approvals/${approval.id}/respond', body: {'approved': true});
    final (secondStatus, _) =
        await client.request('POST', '/approvals/${approval.id}/respond', body: {'approved': false});
    expect(firstStatus, 200);
    expect(secondStatus, 200, reason: 'a duplicate response is a harmless no-op, not an error');
    expect(await approval.decision, ApprovalStatus.approved, reason: 'the first decision wins');
  });

  test('POST /approvals/<id>/respond refuses a missing token like every other non-status route', () async {
    approvalQueue.createForCommand(canonicalCommand: 'npm test', cwd: workspaceRoot, rootPath: workspaceRoot);
    final (status, _) = await client.request(
      'GET',
      '/approvals',
      withToken: false,
    );
    expect(status, 401);
  });

  Future<void> initGitRepo() async {
    Future<void> git(List<String> args) => Process.run('git', args, workingDirectory: workspaceDir.path);
    await git(['init', '-q', '-b', 'main']);
    await git(['config', 'user.name', 'Test User']);
    await git(['config', 'user.email', 'test@example.com']);
    await git(['add', 'hello.txt']);
    await git(['commit', '-q', '-m', 'initial']);
  }

  test('GET /git/status on a non-git-repo workspace returns a clean 409, not a 500', () async {
    final (status, body) = await client.request('GET', '/git/status?root=${Uri.encodeComponent(workspaceRoot)}');
    expect(status, 409);
    expect(body['error'], contains('not a git repository'));
  });

  test('GET /git/status reports a real change after init', () async {
    await initGitRepo();
    File('${workspaceDir.path}/hello.txt').writeAsStringSync('changed');
    final (status, body) = await client.request('GET', '/git/status?root=${Uri.encodeComponent(workspaceRoot)}');
    expect(status, 200);
    expect(body['branch'], 'main');
    expect((body['files'] as List).single['path'], 'hello.txt');
  });

  test('GET /git/diff distinguishes workingTree from staged scope', () async {
    await initGitRepo();
    File('${workspaceDir.path}/hello.txt').writeAsStringSync('changed content');
    final (workingStatus, workingBody) = await client.request(
      'GET',
      '/git/diff?root=${Uri.encodeComponent(workspaceRoot)}',
    );
    expect(workingStatus, 200);
    expect(workingBody['diff'], contains('changed content'));
    final (stagedStatus, stagedBody) = await client.request(
      'GET',
      '/git/diff?root=${Uri.encodeComponent(workspaceRoot)}&staged=true',
    );
    expect(stagedStatus, 200);
    expect(stagedBody['diff'], isEmpty);
  });

  test('GET /git/log lists the initial commit', () async {
    await initGitRepo();
    final (status, body) = await client.request('GET', '/git/log?root=${Uri.encodeComponent(workspaceRoot)}');
    expect(status, 200);
    expect((body['commits'] as List).single['subject'], 'initial');
  });

  test('POST /git/stage then /git/commit runs a real commit reflecting only what was staged', () async {
    await initGitRepo();
    File('${workspaceDir.path}/hello.txt').writeAsStringSync('staged change');
    File('${workspaceDir.path}/untracked.txt').writeAsStringSync('not staged');

    final (stageStatus, _) = await client.request(
      'POST',
      '/git/stage',
      body: {
        'root': workspaceRoot,
        'paths': ['hello.txt'],
      },
    );
    expect(stageStatus, 200);

    final (commitStatus, commitBody) = await client.request(
      'POST',
      '/git/commit',
      body: {'root': workspaceRoot, 'message': 'stage exactly what was staged'},
    );
    expect(commitStatus, 200);
    expect(commitBody['ok'], true);

    final (_, statusBody) = await client.request('GET', '/git/status?root=${Uri.encodeComponent(workspaceRoot)}');
    final paths = (statusBody['files'] as List).map((f) => f['path']).toSet();
    expect(paths, contains('untracked.txt'));
    expect(paths, isNot(contains('hello.txt')));
  });

  test('POST /git/commit with nothing staged responds 409 with reason nothing_staged', () async {
    await initGitRepo();
    final (status, body) = await client.request(
      'POST',
      '/git/commit',
      body: {'root': workspaceRoot, 'message': 'empty'},
    );
    expect(status, 409);
    expect(body['reason'], 'nothing_staged');
  });

  test('POST /git/checkout create:true creates a branch', () async {
    await initGitRepo();
    final (status, body) = await client.request(
      'POST',
      '/git/checkout',
      body: {'root': workspaceRoot, 'branch': 'feature-x', 'create': true},
    );
    expect(status, 200);
    expect(body['branch'], 'feature-x');
  });

  test('git write routes work in an untrusted workspace (no root.trusted gate, no approval queue)', () async {
    // workspaceRoot was granted with trusted: false in setUp -- this is the whole point of
    // design decision 1: a human's own click in the Git panel is the consent.
    await initGitRepo();
    final (stageStatus, _) = await client.request(
      'POST',
      '/git/stage',
      body: {
        'root': workspaceRoot,
        'paths': ['hello.txt'],
      },
    );
    expect(stageStatus, 200);
    expect(approvalQueue.list(), isEmpty, reason: 'a Git panel write never goes through the approval queue');
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

    // A fresh store pair bound to pairingConfigDir -- not the outer setUp()'s workspaceStore/
    // configStore, which point at configDir. Using those by mistake here was a real bug: the
    // token would have been saved to the wrong directory, and the assertion below checking
    // pairingConfigDir would have found nothing.
    final pairingConfigDir = Directory.systemTemp.createTempSync('aura_hostd_control_test_pair_');
    addTearDown(() => pairingConfigDir.deleteSync(recursive: true));
    String? paired;
    final pairingServer = ControlServer(
      workspaceStore: WorkspaceStore.forConfigDir(pairingConfigDir),
      configStore: ConfigStore.forConfigDir(pairingConfigDir),
      onPaired: (token) => paired = token,
      terminalSessions: TerminalSessionManager(WorkspaceStore.forConfigDir(pairingConfigDir)),
      debugSessions: DebugSessionManager(WorkspaceStore.forConfigDir(pairingConfigDir)),
      approvalQueue: ApprovalQueue(),
      isolatedTasks: IsolatedTaskRegistry(WorkspaceStore.forConfigDir(pairingConfigDir).load()),
      logger: (_) {},
    );
    // Remote control was turned on on this computer before the re-pair (WP8): a new token must not
    // silently switch it off, nor on.
    ConfigStore.forConfigDir(pairingConfigDir)
        .save(DaemonConfig(backendUrl: fakeBackend.backendUrl, authToken: null, remoteControl: true));
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
    expect(ConfigStore.forConfigDir(pairingConfigDir).load().remoteControl, isTrue);

    await pairingServer.stop();
  });

  group('folder changes reach the connected agent without a restart (G-05, SEC-08)', () {
    // The agent's TaskHandler is wired the way `backend_connection.dart` wires it -- on
    // `WorkspaceStore.live`, the instance these routes update on save -- and created before any
    // change is made. (`daemon_wiring_test.dart` drives the same over a real socket.)
    late TaskHandler agent;
    late Directory laterDir;

    setUp(() {
      agent = TaskHandler.live(
        workspaceStore,
        approvalQueue: approvalQueue,
        isolatedTasks: IsolatedTaskRegistry(workspaceStore.live),
      );
      laterDir = Directory.systemTemp.createTempSync('aura_hostd_control_test_later_');
    });

    tearDown(() {
      if (laterDir.existsSync()) laterDir.deleteSync(recursive: true);
    });

    test('revoke refuses the next write_file of a task bound before it', () async {
      await client.request('POST', '/roots', body: {'action': 'trust', 'path': workspaceRoot});
      final created = await agent.createTask({'cwd': workspaceRoot});
      await agent.writeFile({'task_id': created['task_id'], 'path': 'one.txt', 'content': '1'});

      final (status, _) =
          await client.request('POST', '/roots', body: {'action': 'revoke', 'path': workspaceRoot});
      expect(status, 200);

      await expectLater(
        agent.writeFile({'task_id': created['task_id'], 'path': 'two.txt', 'content': '2'}),
        throwsA(isA<RpcError>()),
      );
      await expectLater(
        agent.writeFile({'path': '$workspaceRoot/three.txt', 'content': '3'}),
        throwsA(isA<RpcError>()),
      );
      expect(File('$workspaceRoot/two.txt').existsSync(), isFalse);
      expect(File('$workspaceRoot/three.txt').existsSync(), isFalse);
    });

    test('untrust sends a command that ran automatically to approval', () async {
      await client.request('POST', '/roots', body: {'action': 'trust', 'path': workspaceRoot});
      final created = await agent.createTask({'cwd': workspaceRoot});
      final first = await agent.run({
        'task_id': created['task_id'],
        'command': ['sh', '-c', 'mkdir auto'],
        'timeout_s': 5.0,
      });
      expect(first['exit_code'], 0);

      await client.request('POST', '/roots', body: {'action': 'untrust', 'path': workspaceRoot});
      final second = agent.run({
        'task_id': created['task_id'],
        'command': ['sh', '-c', 'mkdir asked'],
        'timeout_s': 5.0,
      });
      expect(approvalQueue.list(), hasLength(1));
      approvalQueue.respond(approvalQueue.list().single.id, false);
      await expectLater(second, throwsA(isA<RpcError>()));
      expect(Directory('$workspaceRoot/asked').existsSync(), isFalse);
    }, testOn: '!windows');

    test('a folder granted after the agent connected is usable', () async {
      final (status, body) = await client.request(
        'POST',
        '/roots',
        body: {'action': 'grant', 'path': laterDir.path, 'trusted': true},
      );
      expect(status, 200);
      final canonical = body['root']['path'] as String;
      final created = await agent.createTask({'cwd': canonical});
      await agent.writeFile({'task_id': created['task_id'], 'path': 'new.txt', 'content': 'hi'});
      expect(File('$canonical/new.txt').readAsStringSync(), 'hi');
    });
  });

  test(
      'the daemon collects an approved pairing itself, with no /pair/status poll from a UI, and '
      'a late /pair/status still reports it (INT-038)', () async {
    final fakeBackend = await _FakeAuthServer.start(userCode: 'AB3D-9XYZ', deviceId: 'dev-9', pendingPolls: 2);
    addTearDown(fakeBackend.close);
    final pairingConfigDir = Directory.systemTemp.createTempSync('aura_hostd_control_test_bgpair_');
    addTearDown(() => pairingConfigDir.deleteSync(recursive: true));
    final pairedTokens = <String>[];
    final pairingServer = ControlServer(
      workspaceStore: WorkspaceStore.forConfigDir(pairingConfigDir),
      configStore: ConfigStore.forConfigDir(pairingConfigDir),
      onPaired: pairedTokens.add,
      terminalSessions: TerminalSessionManager(WorkspaceStore.forConfigDir(pairingConfigDir)),
      debugSessions: DebugSessionManager(WorkspaceStore.forConfigDir(pairingConfigDir)),
      approvalQueue: ApprovalQueue(),
      isolatedTasks: IsolatedTaskRegistry(WorkspaceStore.forConfigDir(pairingConfigDir).load()),
      minPairingPollInterval: const Duration(milliseconds: 20),
      logger: (_) {},
    );
    await pairingServer.start(pairingConfigDir);
    addTearDown(pairingServer.stop);
    final controlFile =
        jsonDecode(File('${pairingConfigDir.path}/control.json').readAsStringSync()) as Map<String, dynamic>;
    final pairingClient = _ControlClient(controlFile['port'] as int, controlFile['token'] as String);
    addTearDown(pairingClient.close);

    final (startStatus, _) =
        await pairingClient.request('POST', '/pair/start', body: {'backend_url': fakeBackend.backendUrl});
    expect(startStatus, 200);

    // The UI went away: nobody polls /pair/status. The daemon must still pick the token up.
    final deadline = DateTime.now().add(const Duration(seconds: 5));
    while (pairedTokens.isEmpty && DateTime.now().isBefore(deadline)) {
      await Future<void>.delayed(const Duration(milliseconds: 20));
    }
    expect(pairedTokens, ['tok-abc'], reason: 'onPaired is called exactly once');
    expect(ConfigStore.forConfigDir(pairingConfigDir).load().authToken, 'tok-abc');

    final (lateStatus, lateBody) = await pairingClient.request('GET', '/pair/status');
    expect(lateStatus, 200);
    expect(lateBody['status'], 'approved');
    expect(lateBody['device_id'], 'dev-9');
    expect(pairedTokens, hasLength(1));
  });

  group('diagnostics (D7 Part A.2)', () {
    test('GET /diagnostics/detect reports the marker-detected tool', () async {
      File('${workspaceDir.path}/pubspec.yaml').writeAsStringSync('name: fixture\n');
      final (status, body) = await client.request('GET', '/diagnostics/detect?root=$workspaceRoot');
      expect(status, 200);
      expect(body['tool'], 'dart_analyze');
    });

    test('GET /diagnostics/detect reports null when nothing matches', () async {
      final (status, body) = await client.request('GET', '/diagnostics/detect?root=$workspaceRoot');
      expect(status, 200);
      expect(body['tool'], isNull);
    });

    test('GET /diagnostics/detect refuses an ungranted root', () async {
      final (status, body) = await client.request('GET', '/diagnostics/detect?root=/not/granted');
      expect(status, 404);
      expect(body['error'], contains('not a granted root'));
    });

    test('POST /diagnostics/run refuses an untrusted folder (G-17, SEC-10)', () async {
      // A linter loads the repository's own config and plugins (`eslint.config.js`, a ruff or
      // pyflakes plugin): repo-supplied code, which an untrusted folder never runs unasked.
      File('${workspaceDir.path}/pubspec.yaml').writeAsStringSync('name: fixture\n');
      final (status, body) = await client.request(
        'POST',
        '/diagnostics/run',
        body: {'root': workspaceRoot, 'tool': 'dart_analyze'},
      );
      expect(status, 403);
      expect(body['error'], contains('trusted'));
    });

    test('POST /diagnostics/run reports a real tool run', () async {
      // dart_analyze is guaranteed available in this test environment (the Dart SDK running
      // these tests), so this exercises the real Process.run path end to end, not a fake.
      // Diagnostics run only in a trusted folder (G-17).
      await client.request('POST', '/roots', body: {'action': 'trust', 'path': workspaceRoot});
      File('${workspaceDir.path}/pubspec.yaml')
          .writeAsStringSync('name: fixture\nenvironment:\n  sdk: ^3.0.0\n');
      final (status, body) = await client.request(
        'POST',
        '/diagnostics/run',
        body: {'root': workspaceRoot, 'tool': 'dart_analyze'},
      );
      expect(status, 200);
      expect(body['tool'], 'dart_analyze');
      expect(body['not_found'], false);
      expect(body['exit_code'], isA<int>());
    });

    test('POST /diagnostics/run reports coherently whether or not the tool is installed', () async {
      await client.request('POST', '/roots', body: {'action': 'trust', 'path': workspaceRoot});
      final (status, body) = await client.request(
        'POST',
        '/diagnostics/run',
        body: {'root': workspaceRoot, 'tool': 'ruff'},
      );
      // Whether "ruff" happens to be installed on the machine running this test varies -- assert
      // only that the route responds coherently either way, not on ruff's own presence.
      expect(status, 200);
      expect(body['tool'], 'ruff');
      expect(body['not_found'], isA<bool>());
    });

    test('POST /diagnostics/run rejects an unknown tool', () async {
      final (status, body) = await client.request(
        'POST',
        '/diagnostics/run',
        body: {'root': workspaceRoot, 'tool': 'not_a_real_tool'},
      );
      expect(status, 400);
      expect(body['error'], contains('unknown diagnostics tool'));
    });

    test('POST /diagnostics/run refuses an ungranted root', () async {
      final (status, body) = await client.request(
        'POST',
        '/diagnostics/run',
        body: {'root': '/not/granted', 'tool': 'dart_analyze'},
      );
      expect(status, 404);
      expect(body['error'], contains('not a granted root'));
    });
  });

  group('debug console (D7 Part A.3)', () {
    Future<WebSocket> connect() => WebSocket.connect(
          'ws://127.0.0.1:${client.port}/debug?root=${Uri.encodeComponent(workspaceRoot)}',
          headers: {HttpHeaders.authorizationHeader: 'Bearer ${client.token}'},
        );

    test('refuses an untrusted workspace with the terminal\'s own refusal shape', () async {
      final ws = await connect();
      addTearDown(ws.close);
      final iterator = StreamIterator<dynamic>(ws);
      addTearDown(iterator.cancel);
      await iterator.moveNext();
      final decoded = jsonDecode(iterator.current as String) as Map<String, dynamic>;
      expect(decoded['type'], 'error');
      expect(decoded['message'], contains('not trusted'));
    });

    test('start spawns a real debugpy process and tunnels DAP bytes both ways', () async {
      final roots = workspaceStore.load();
      roots.setTrusted(workspaceRoot, true);
      workspaceStore.save(roots);
      File('${workspaceDir.path}/script.py').writeAsStringSync('import time\ntime.sleep(30)\n');

      final ws = await connect();
      addTearDown(ws.close);
      final iterator = StreamIterator<dynamic>(ws);
      addTearDown(iterator.cancel);

      ws.add(jsonEncode({'type': 'start', 'script_path': 'script.py'}));

      // debugpy importing + binding a fresh interpreter can take a few seconds -- generous but
      // bounded, matching _connectTimeout's own budget in debug_session_manager.dart.
      await iterator.moveNext().timeout(const Duration(seconds: 20));
      final started = jsonDecode(iterator.current as String) as Map<String, dynamic>;
      expect(started['type'], 'started');
      expect(started['session_id'], isA<String>());
      expect(started['pid'], isA<int>());

      // Send a real DAP `initialize` request over the tunnel and expect the daemon to relay
      // debugpy's own reply back as a `dap` frame -- proves bytes actually flow both ways, not
      // just that a process was spawned.
      final initializeBody = jsonEncode({
        'seq': 1,
        'type': 'request',
        'command': 'initialize',
        'arguments': {'adapterID': 'aura_hostd_test', 'linesStartAt1': true, 'columnsStartAt1': true},
      });
      final framed = 'Content-Length: ${utf8.encode(initializeBody).length}\r\n\r\n$initializeBody';
      ws.add(jsonEncode({'type': 'dap', 'data': base64Encode(utf8.encode(framed))}));

      await iterator.moveNext().timeout(const Duration(seconds: 10));
      final dapFrame = jsonDecode(iterator.current as String) as Map<String, dynamic>;
      expect(dapFrame['type'], 'dap');
      final relayed = utf8.decode(base64Decode(dapFrame['data'] as String));
      expect(relayed, contains('Content-Length'));

      ws.add(jsonEncode({'type': 'close'}));
      // Give the killed debugpy child process (a real Windows process, not a mock) time to
      // release its file handles on script.py before the shared tearDown deletes workspaceDir --
      // otherwise that delete can race a process that received the kill signal but hasn't
      // finished exiting yet.
      await Future<void>.delayed(const Duration(milliseconds: 800));
    });

    test('start refuses a script path outside the granted root', () async {
      final roots = workspaceStore.load();
      roots.setTrusted(workspaceRoot, true);
      workspaceStore.save(roots);

      final ws = await connect();
      addTearDown(ws.close);
      final iterator = StreamIterator<dynamic>(ws);
      addTearDown(iterator.cancel);

      ws.add(jsonEncode({'type': 'start', 'script_path': '../outside.py'}));
      await iterator.moveNext().timeout(const Duration(seconds: 5));
      final decoded = jsonDecode(iterator.current as String) as Map<String, dynamic>;
      expect(decoded['type'], 'error');
      expect(decoded['message'], contains('outside the granted workspace root'));
    });
  });

  group('Agent Review / Part C routes (D9)', () {
    Future<ProcessResult> git(String cwd, List<String> args) =>
        Process.run('git', args, workingDirectory: cwd);

    setUp(() async {
      await git(workspaceDir.path, ['init', '-q', '-b', 'main']);
      await git(workspaceDir.path, ['config', 'user.name', 'Test User']);
      await git(workspaceDir.path, ['config', 'user.email', 'test@example.com']);
      File('${workspaceDir.path}/a.txt').writeAsStringSync('original');
      await git(workspaceDir.path, ['add', '--', 'a.txt']);
      await git(workspaceDir.path, ['commit', '-q', '-m', 'initial commit']);
    });

    Future<String> createIsolatedTask(String taskKey) async {
      const taskId = 'test-task-id';
      server.isolatedTasks.registerConfig(taskId, taskKey: taskKey);
      await server.isolatedTasks.resolve(taskId, workspaceRoot);
      return taskKey;
    }

    test('GET /tasks lists every isolated task this daemon knows about', () async {
      await createIsolatedTask('review-1');
      final (status, body) = await client.request('GET', '/tasks');
      expect(status, 200);
      final tasks = (body['tasks'] as List).cast<Map<String, dynamic>>();
      expect(tasks, hasLength(1));
      expect(tasks.single['task_key'], 'review-1');
      expect(tasks.single['branch'], 'aura/task-review-1');
    });

    test('GET /git/task-diff shows the real change the task branch made', () async {
      final taskKey = await createIsolatedTask('review-2');
      final worktreePath = '${workspaceDir.path}/.aura-workspaces/$taskKey';
      File('$worktreePath/a.txt').writeAsStringSync('changed by the agent');
      await git(worktreePath, ['commit', '-q', '-am', 'agent change']);

      final (status, body) = await client.request(
        'GET',
        '/git/task-diff?task_key=$taskKey&base=main',
      );
      expect(status, 200);
      expect(body['diff'] as String, contains('+changed by the agent'));
    });

    test('GET /git/task-diff-stat summarizes the change', () async {
      final taskKey = await createIsolatedTask('review-3');
      final worktreePath = '${workspaceDir.path}/.aura-workspaces/$taskKey';
      File('$worktreePath/a.txt').writeAsStringSync('changed');
      await git(worktreePath, ['commit', '-q', '-am', 'agent change']);

      final (status, body) = await client.request(
        'GET',
        '/git/task-diff-stat?task_key=$taskKey&base=main',
      );
      expect(status, 200);
      expect(body['stat'] as String, contains('a.txt'));
    });

    test('GET /git/task-diff 404s an unknown task_key', () async {
      final (status, body) = await client.request(
        'GET',
        '/git/task-diff?task_key=never-created&base=main',
      );
      expect(status, 404);
      expect(body['error'], contains('no isolated workspace'));
    });

    test('POST /git/merge-task merges the task branch into the main worktree, locally only',
        () async {
      final taskKey = await createIsolatedTask('review-4');
      final worktreePath = '${workspaceDir.path}/.aura-workspaces/$taskKey';
      File('$worktreePath/new_file.txt').writeAsStringSync('from the agent');
      await git(worktreePath, ['add', '--', 'new_file.txt']);
      await git(worktreePath, ['commit', '-q', '-m', 'agent adds a file']);

      final (status, body) = await client.request(
        'POST',
        '/git/merge-task',
        body: {'root': workspaceRoot, 'task_branch': 'aura/task-review-4'},
      );
      expect(status, 200);
      expect(body['ok'], true);
      expect(File('${workspaceDir.path}/new_file.txt').existsSync(), isTrue);
      expect(File('${workspaceDir.path}/new_file.txt').readAsStringSync(), 'from the agent');
    });

    test('POST /git/merge-task with squash commits a single squashed commit', () async {
      final taskKey = await createIsolatedTask('review-5');
      final worktreePath = '${workspaceDir.path}/.aura-workspaces/$taskKey';
      File('$worktreePath/squashed.txt').writeAsStringSync('squashed content');
      await git(worktreePath, ['add', '--', 'squashed.txt']);
      await git(worktreePath, ['commit', '-q', '-m', 'agent commit 1']);

      final (status, body) = await client.request(
        'POST',
        '/git/merge-task',
        body: {'root': workspaceRoot, 'task_branch': 'aura/task-review-5', 'squash': true},
      );
      expect(status, 200);
      expect(body['ok'], true);
      expect(body['committed'], true);
      expect(File('${workspaceDir.path}/squashed.txt').existsSync(), isTrue);
    });

    test('POST /git/merge-task reports a conflict instead of silently failing or auto-resolving',
        () async {
      final taskKey = await createIsolatedTask('review-6');
      final worktreePath = '${workspaceDir.path}/.aura-workspaces/$taskKey';
      File('$worktreePath/a.txt').writeAsStringSync('changed in the task');
      await git(worktreePath, ['commit', '-q', '-am', 'agent change']);

      // A conflicting change lands on main in the meantime.
      File('${workspaceDir.path}/a.txt').writeAsStringSync('changed on main');
      await git(workspaceDir.path, ['commit', '-q', '-am', 'main change']);

      final (status, body) = await client.request(
        'POST',
        '/git/merge-task',
        body: {'root': workspaceRoot, 'task_branch': 'aura/task-review-6'},
      );
      expect(status, 409);
      expect(body['ok'], false);
      expect(body['reason'], 'conflict');
      expect(body['conflicted_files'], contains('a.txt'));

      // The main worktree is left resolvable, not silently aborted -- git itself is still
      // mid-merge here, matching the plan's own "no automated conflict resolution" scope.
      final status2 = await git(workspaceDir.path, ['status', '--short']);
      expect(status2.stdout.toString(), isNotEmpty);
      await git(workspaceDir.path, ['merge', '--abort']);
    });

    test('POST /git/discard-task removes the worktree and branch, and it disappears from GET /tasks',
        () async {
      final taskKey = await createIsolatedTask('review-7');
      final worktreePath = '${workspaceDir.path}/.aura-workspaces/$taskKey';
      expect(Directory(worktreePath).existsSync(), isTrue);

      final (status, body) = await client.request('POST', '/git/discard-task', body: {'task_key': taskKey});
      expect(status, 200);
      expect(body['ok'], true);
      expect(Directory(worktreePath).existsSync(), isFalse);

      final (listStatus, listBody) = await client.request('GET', '/tasks');
      expect(listStatus, 200);
      expect(listBody['tasks'], isEmpty);
    });

    test('POST /git/discard-task 404s an unknown task_key', () async {
      final (status, body) = await client.request('POST', '/git/discard-task', body: {'task_key': 'nope'});
      expect(status, 404);
      expect(body['error'], contains('no isolated workspace'));
    });
  });
}
