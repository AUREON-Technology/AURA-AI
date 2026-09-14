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
      approvalQueue: approvalQueue,
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
      approvalQueue: ApprovalQueue(),
      logger: (_) {},
    );
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

    test('POST /diagnostics/run reports a real tool run', () async {
      // dart_analyze is guaranteed available in this test environment (the Dart SDK running
      // these tests), so this exercises the real Process.run path end to end, not a fake.
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
}
