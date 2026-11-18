/// `aura_hostd` -- the local daemon that lets AURA's Engineer agent act on this machine's real
/// files and terminal. See `docs/architecture/011-desktop-engineer-agent.md` for the design and
/// `aura_hostd/README.md` for usage.
///
/// Subcommands:
///   aura_hostd auth set-token &lt;token&gt; [--backend-url &lt;url&gt;]
///   aura_hostd auth pair [--backend-url &lt;url&gt;]
///   aura_hostd grant &lt;path&gt; [--trust]
///   aura_hostd revoke &lt;path&gt;
///   aura_hostd trust &lt;path&gt;
///   aura_hostd untrust &lt;path&gt;
///   aura_hostd roots
///   aura_hostd run                      # start the daemon (connects and serves until killed)
///
/// `run` also starts a loopback-only local control server (`lib/src/control_server.dart`, Unit 8)
/// so a UI on the same machine -- `aura_client`'s desktop Engineer workspace screen -- can
/// supervise this daemon and browse granted workspaces without shelling out to this CLI and
/// scraping stdout.
library;

import 'dart:async';
import 'dart:io';

import 'package:aura_hostd/src/approval_queue.dart';
import 'package:aura_hostd/src/backend_connection.dart';
import 'package:aura_hostd/src/config.dart';
import 'package:aura_hostd/src/containment.dart';
import 'package:aura_hostd/src/control_server.dart';
import 'package:aura_hostd/src/debug_session_manager.dart';
import 'package:aura_hostd/src/isolated_task_registry.dart';
import 'package:aura_hostd/src/pairing_client.dart';
import 'package:aura_hostd/src/terminal_session_manager.dart';
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
      _requireArgs(args, 2, 'auth set-token <token> [--backend-url <url>] | auth pair [--backend-url <url>]');
      var backendUrl = configStore.load().backendUrl;
      final flagIndex = args.indexOf('--backend-url');
      if (flagIndex >= 0 && flagIndex + 1 < args.length) {
        backendUrl = args[flagIndex + 1];
      }
      if (args[1] == 'set-token') {
        _requireArgs(args, 3, 'auth set-token <token> [--backend-url <url>]');
        configStore.saveCredentials(backendUrl: backendUrl, authToken: args[2]);
        stdout.writeln('Saved auth token for $backendUrl');
      } else if (args[1] == 'pair') {
        await _pair(configStore, backendUrl);
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

    case 'roots':
      final roots = workspaceStore.load();
      if (roots.all.isEmpty) {
        stdout.writeln('No workspace roots granted. Run `aura_hostd grant <path>`.');
      }
      for (final root in roots.all) {
        stdout.writeln('${root.canonicalPath}${root.trusted ? "  [trusted]" : ""}');
      }
      break;

    case 'run':
      // Last line of defence (audit INT-019): an error escaping some un-awaited async path -- a
      // late send on a dropped connection, a handler that forgot a catch -- is logged instead of
      // killing a daemon the user expects to stay up. The paths known to do it are fixed at the
      // source (`rpc_client.dart`'s `_send`); this only stops the next one being fatal.
      await runZonedGuarded(
        () => _runDaemon(configStore, workspaceStore),
        (error, stack) => stderr.writeln('[aura_hostd] unhandled error (daemon kept running): $error\n$stack'),
      );
      break;

    default:
      _printUsage();
      exit(64);
  }
}

/// `aura_hostd auth pair` -- ADR-011's pairing flow, daemon side. Starts a pairing session,
/// prints the code for the user to enter into an already-signed-in AURA client, polls until
/// they approve it, then saves the resulting device-scoped token exactly like `auth set-token`
/// would have -- this is the flow that token was previously expected to arrive via out-of-band.
Future<void> _pair(ConfigStore configStore, String backendUrl) async {
  stdout.writeln('Pairing with $backendUrl ...');
  try {
    final result = await pairWithCode(
      backendUrl,
      onCode: (userCode) {
        stdout.writeln('');
        stdout.writeln('  Enter this code in AURA: $userCode');
        stdout.writeln('');
        stdout.writeln('Waiting for approval...');
      },
    );
    configStore.saveCredentials(backendUrl: backendUrl, authToken: result.accessToken);
    stdout.writeln('Paired. Device ID: ${result.deviceId}');
  } on PairingFailedError catch (error) {
    stderr.writeln('Pairing failed: ${error.message}');
    exit(1);
  }
}

Future<void> _runDaemon(ConfigStore configStore, WorkspaceStore workspaceStore) async {
  // The one live set of grants (audit INT-002): the control server's root actions update it on
  // save, and every agent RPC resolves against it -- never a start-up snapshot.
  final roots = workspaceStore.live;
  if (roots.all.isEmpty) {
    stdout.writeln(
      'Warning: no workspace roots granted yet -- every write_file/run_command call will be '
      'refused until you run `aura_hostd grant <path>`.',
    );
  }

  final approvalQueue = ApprovalQueue();
  // D8/D9: one registry, shared between TaskHandler (agent-facing, created below once paired)
  // and ControlServer (human-facing "Agent Review" panel) -- see
  // isolated_task_registry.dart's own doc comment for why a separate instance per side would
  // silently break Part C.
  final isolatedTasks = IsolatedTaskRegistry(roots);
  final backend = BackendConnection(
    configStore: configStore,
    workspaceStore: workspaceStore,
    approvalQueue: approvalQueue,
    isolatedTasks: isolatedTasks,
  );
  final controlServer = ControlServer(
    workspaceStore: workspaceStore,
    configStore: configStore,
    // A UI driving `/pair/start` + `/pair/status` (Unit 8, control_server.dart) pairs this
    // daemon without ever needing `run` restarted -- the control server exists before any token
    // does precisely so that path works.
    // A new token replaces whatever connection the old one had -- including one the backend
    // already refused (4401), which used to make a re-pair a silent no-op (INT-003).
    onPaired: (_) => backend.restart(),
    terminalSessions: TerminalSessionManager(workspaceStore),
    debugSessions: DebugSessionManager(workspaceStore),
    approvalQueue: approvalQueue,
    isolatedTasks: isolatedTasks,
    isBackendConnected: () => backend.isConnected,
    isCredentialsRejected: () => backend.credentialsRejected,
    lastCloseCode: () => backend.lastCloseCode,
  );
  await controlServer.start(resolveConfigDir());

  backend.startIfAuthenticated();
  if (configStore.load().authToken == null) {
    stdout.writeln(
      'No auth token configured yet -- run `aura_hostd auth pair`, or pair from the AURA app, '
      'to connect to the backend. The control server is up in the meantime.',
    );
  }

  var shuttingDown = false;
  Future<void> shutdown() async {
    if (shuttingDown) return;
    shuttingDown = true;
    stdout.writeln('Shutting down...');
    backend.stop();
    await controlServer.stop();
    exit(0);
  }

  ProcessSignal.sigint.watch().listen((_) => shutdown());
  if (!Platform.isWindows) {
    ProcessSignal.sigterm.watch().listen((_) => shutdown());
  }

  // Keep the process alive -- the actual work happens in the control server's request handler
  // and (once connected) HostAgentClient.run()'s own loop; this just prevents main() returning.
  await Completer<void>().future;
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
  aura_hostd auth set-token <token> [--backend-url <url>]
  aura_hostd auth pair [--backend-url <url>]
  aura_hostd grant <path> [--trust]
  aura_hostd revoke <path>
  aura_hostd trust <path>
  aura_hostd untrust <path>
  aura_hostd roots
  aura_hostd run
''');
}
