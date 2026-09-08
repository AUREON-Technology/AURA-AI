/// `aura_hostd` -- the local daemon that lets AURA's Engineer agent act on this machine's real
/// files and terminal. See `docs/architecture/011-desktop-engineer-agent.md` for the design and
/// `aura_hostd/README.md` for usage.
///
/// Subcommands:
///   aura_hostd auth set-token &lt;token&gt; [--backend-url &lt;url&gt;]
///   aura_hostd auth pair &lt;code&gt; [--backend-url &lt;url&gt;]
///   aura_hostd grant &lt;path&gt; [--trust]
///   aura_hostd revoke &lt;path&gt;
///   aura_hostd trust &lt;path&gt;
///   aura_hostd untrust &lt;path&gt;
///   aura_hostd yolo on|off
///   aura_hostd roots
///   aura_hostd run                      # start the daemon (connects and serves until killed)
library;

import 'dart:convert';
import 'dart:io';

import 'package:aura_hostd/src/config.dart';
import 'package:aura_hostd/src/containment.dart';
import 'package:aura_hostd/src/control_server.dart';
import 'package:aura_hostd/src/rpc_client.dart';
import 'package:aura_hostd/src/task_handler.dart';
import 'package:aura_hostd/src/workspace_store.dart';

Future<void> main(List<String> args) async {
  if (args.isEmpty) {
    _printUsage();
    exit(64);
  }

  final configDir = resolveConfigDir();
  final configStore = ConfigStore.forConfigDir(configDir);
  final workspaceStore = WorkspaceStore.forConfigDir(configDir);

  switch (args.first) {
    case 'auth':
      _requireArgs(args, 2, 'auth set-token <token> | auth pair <code>');
      if (args[1] == 'set-token') {
        _requireArgs(args, 3, 'auth set-token <token> [--backend-url <url>]');
        final token = args[2];
        var backendUrl = configStore.load().backendUrl;
        final flagIndex = args.indexOf('--backend-url');
        if (flagIndex >= 0 && flagIndex + 1 < args.length) {
          backendUrl = args[flagIndex + 1];
        }
        final previous = configStore.load();
        configStore.save(
          DaemonConfig(
            backendUrl: backendUrl,
            authToken: token,
            deviceToken: previous.deviceToken,
            yolo: previous.yolo,
          ),
        );
        stdout.writeln('Saved auth token for $backendUrl');
      } else if (args[1] == 'pair') {
        _requireArgs(args, 3, 'auth pair <code> [--backend-url <url>]');
        await _pair(configStore, args[2], args);
      } else {
        _printUsage();
        exit(64);
      }
      break;

    case 'grant':
      _requireArgs(args, 2, 'grant <path>');
      final roots = workspaceStore.load();
      final trusted = args.contains('--trust');
      try {
        final root = roots.addRoot(args[1], trusted: trusted);
        workspaceStore.save(roots);
        stdout.writeln('Granted ${root.canonicalPath}${trusted ? " (trusted)" : ""}');
      } on InvalidRootError catch (error) {
        stderr.writeln('Cannot grant: $error');
        exit(1);
      }
      break;

    case 'revoke':
      _requireArgs(args, 2, 'revoke <path>');
      final roots = workspaceStore.load();
      if (roots.removeRoot(args[1])) {
        workspaceStore.save(roots);
        stdout.writeln('Revoked ${args[1]}');
      } else {
        stderr.writeln('${args[1]} was not a granted root');
        exit(1);
      }
      break;

    case 'trust':
    case 'untrust':
      _requireArgs(args, 2, '${args.first} <path>');
      final roots = workspaceStore.load();
      final updated = roots.setTrusted(args[1], args.first == 'trust');
      if (updated == null) {
        stderr.writeln('${args[1]} is not a granted root -- run `aura_hostd grant` first');
        exit(1);
      }
      workspaceStore.save(roots);
      stdout.writeln('${args.first == "trust" ? "Trusted" : "Untrusted"} ${updated.canonicalPath}');
      break;

    case 'yolo':
      _requireArgs(args, 2, 'yolo on|off');
      final enabled = args[1] == 'on';
      if (args[1] != 'on' && args[1] != 'off') {
        stderr.writeln('Usage: aura_hostd yolo on|off');
        exit(64);
      }
      final previous = configStore.load();
      configStore.save(previous.copyWith(yolo: enabled));
      stdout.writeln(
        enabled
            ? 'YOLO on -- commands in any granted root skip the allowlist; '
                'hard denylist and path containment still apply.'
            : 'YOLO off -- untrusted roots use the read-only allowlist again.',
      );
      break;

    case 'roots':
      final roots = workspaceStore.load();
      final config = configStore.load();
      if (config.yolo) {
        stdout.writeln('[YOLO on]');
      }
      if (roots.all.isEmpty) {
        stdout.writeln('No workspace roots granted. Run `aura_hostd grant <path>`.');
      }
      for (final root in roots.all) {
        stdout.writeln('${root.canonicalPath}${root.trusted ? "  [trusted]" : ""}');
      }
      break;

    case 'run':
      await _runDaemon(configStore, workspaceStore);
      break;

    default:
      _printUsage();
      exit(64);
  }
}

Future<void> _pair(ConfigStore configStore, String code, List<String> args) async {
  var backendUrl = configStore.load().backendUrl;
  final flagIndex = args.indexOf('--backend-url');
  if (flagIndex >= 0 && flagIndex + 1 < args.length) {
    backendUrl = args[flagIndex + 1];
  }
  final uri = Uri.parse(backendUrl).replace(path: '/api/v3/auth/desktop/pairing/exchange');
  final client = HttpClient();
  try {
    final request = await client.postUrl(uri);
    request.headers.contentType = ContentType.json;
    request.write(jsonEncode({'code': code.trim().toUpperCase(), 'device_name': 'aura_hostd'}));
    final response = await request.close();
    final body = await utf8.decoder.bind(response).join();
    if (response.statusCode != 200) {
      stderr.writeln('Pairing failed (${response.statusCode}): $body');
      exit(1);
    }
    final decoded = jsonDecode(body) as Map<String, dynamic>;
    final access = decoded['access_token'] as String?;
    final device = decoded['device_token'] as String?;
    if (access == null || access.isEmpty) {
      stderr.writeln('Pairing response missing access_token');
      exit(1);
    }
    final previous = configStore.load();
    configStore.save(
      DaemonConfig(
        backendUrl: backendUrl,
        authToken: access,
        deviceToken: device,
        yolo: previous.yolo,
      ),
    );
    stdout.writeln('Paired with $backendUrl (device token saved). Run `aura_hostd run`.');
  } finally {
    client.close(force: true);
  }
}

Future<void> _runDaemon(ConfigStore configStore, WorkspaceStore workspaceStore) async {
  final config = configStore.load();
  if (config.authToken == null || config.authToken!.isEmpty) {
    stderr.writeln(
      'No auth token configured. Run `aura_hostd auth pair <code>` '
      '(or `auth set-token <token>`) first.',
    );
    exit(1);
  }
  // Reload roots on each policy decision via the store; the in-memory set used by TaskHandler
  // is seeded once and refreshed when the control plane mutates grants.
  var roots = workspaceStore.load();
  if (roots.all.isEmpty) {
    stdout.writeln(
      'Warning: no workspace roots granted yet -- every write_file/run_command call will be '
      'refused until you run `aura_hostd grant <path>` (or use the desktop UI).',
    );
  }

  final handler = TaskHandler(roots, yolo: config.yolo);
  final client = HostAgentClient(
    wsUrl: config.hostAgentWsUrl,
    authToken: config.authToken!,
    taskHandler: handler,
  );

  final controlPort = int.tryParse(Platform.environment['AURA_HOSTD_CONTROL_PORT'] ?? '') ??
      defaultControlPort;
  final control = ControlServer(
    configStore: configStore,
    workspaceStore: workspaceStore,
    roots: roots,
    handler: handler,
    isConnected: () => client.isConnected,
    port: controlPort,
  );
  await control.start();
  stdout.writeln('Control plane listening on http://127.0.0.1:$controlPort');
  if (config.yolo) {
    stdout.writeln('YOLO is ON -- hard denylist and path containment still apply.');
  }

  var shuttingDown = false;
  Future<void> shutdown() async {
    if (shuttingDown) return;
    shuttingDown = true;
    stdout.writeln('Shutting down...');
    await control.stop();
    client.stop();
    exit(0);
  }

  ProcessSignal.sigint.watch().listen((_) => shutdown());
  if (!Platform.isWindows) {
    ProcessSignal.sigterm.watch().listen((_) => shutdown());
  }

  await client.run();
}

void _requireArgs(List<String> args, int minLength, String usage) {
  if (args.length < minLength) {
    stderr.writeln('Usage: aura_hostd $usage');
    exit(64);
  }
}

void _printUsage() {
  stdout.writeln('''
aura_hostd -- local daemon for AURA's desktop Engineer agent

Usage:
  aura_hostd auth pair <code> [--backend-url <url>]
  aura_hostd auth set-token <token> [--backend-url <url>]
  aura_hostd grant <path> [--trust]
  aura_hostd revoke <path>
  aura_hostd trust <path>
  aura_hostd untrust <path>
  aura_hostd yolo on|off
  aura_hostd roots
  aura_hostd run
''');
}
