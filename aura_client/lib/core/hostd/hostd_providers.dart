/// Riverpod wiring for the desktop Engineer daemon screen (ADR-011 Unit 8): the control client,
/// the process supervisor, a polled status snapshot, and the executable-path setting a person
/// configures once -- there is no bundled `aura_hostd` binary to default to, unlike
/// `google_desktop_client_id` on the backend, which is a registered OAuth client id now
/// hardcoded rather than left for an operator to supply.
library;

import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../security/aura_secure_storage.dart';
import 'hostd_bundled_path.dart';
import 'hostd_control_client.dart';
import 'hostd_supervisor.dart';

class HostdRoot {
  HostdRoot({required this.path, required this.trusted});

  final String path;
  final bool trusted;

  factory HostdRoot.fromJson(Map<String, dynamic> json) => HostdRoot(
        path: json['path'] as String,
        trusted: json['trusted'] as bool? ?? false,
      );
}

class HostdStatusSnapshot {
  HostdStatusSnapshot({
    required this.reachable,
    this.authenticated = false,
    this.backendConnected,
    this.backendUrl,
    this.roots = const [],
    this.error,
  });

  /// Whether the control server answered at all -- false means `aura_hostd run` isn't up (or
  /// this platform has no control file for this app to have found in the first place).
  final bool reachable;
  final bool authenticated;
  /// Whether the daemon's actual socket to the backend is open right now -- distinct from
  /// [authenticated] (has a stored pairing token), which stays true for as long as a post-drop
  /// reconnect is in backoff (e.g. during a backend redeploy). Without this, "Host Connected"
  /// kept showing green while every tool call was actually failing with "no desktop app is
  /// connected" -- confirmed live against a real session.
  ///
  /// `null` means the daemon's `/status` did not report the field at all, which only an
  /// `aura_hostd` older than the commit that added it does -- a version skew between a rebuilt
  /// app and a stale daemon binary. That is *not* the same as `false`: defaulting it to `false`
  /// pinned the UI to "reconnecting..." forever against a daemon that was in fact connected
  /// (confirmed live -- the backend's own `host agent connected` log said so while the panel
  /// claimed otherwise), with nothing on screen to suggest rebuilding the daemon. Every call
  /// site renders this third state as "status unknown, update aura_hostd" instead.
  final bool? backendConnected;
  final String? backendUrl;
  final List<HostdRoot> roots;
  final String? error;

  /// Whether the daemon holds a pairing for a *different* server than [appBackendUrl], the one
  /// this app is signed in to.
  ///
  /// The daemon keeps its own `config.json` (backend URL plus device token), written when it was
  /// last paired, and keeps dialling that URL for as long as it runs. When the backend moves --
  /// found live 2026-11-17, after the production backend moved to `api.aureon-ai.tech` -- a
  /// daemon paired to the old host retries a dead address forever: the status bar said
  /// "Reconnecting to server..." indefinitely and the Engineer reported it could not touch any
  /// file. Nothing offered a way out, because Pair was only shown to an *unpaired* daemon. This is
  /// the check that makes the difference visible, and re-pairing the way out.
  ///
  /// False when the daemon is not paired (nothing to be stale) or did not report its URL (an
  /// older aura_hostd), so neither case raises a false alarm.
  bool pairedToDifferentServer(String appBackendUrl) {
    final daemonUrl = backendUrl;
    if (!authenticated || daemonUrl == null || daemonUrl.trim().isEmpty) return false;
    return !sameBackendServer(daemonUrl, appBackendUrl);
  }
}

/// Whether two backend base URLs name the same server: same scheme, host and port, ignoring case,
/// a trailing slash and an explicit default port. Anything that does not parse as a URL falls back
/// to a trimmed, case-insensitive comparison rather than reporting a difference it cannot prove.
bool sameBackendServer(String a, String b) {
  final left = Uri.tryParse(a.trim());
  final right = Uri.tryParse(b.trim());
  if (left == null || right == null || !left.hasAuthority || !right.hasAuthority) {
    String bare(String value) => value.trim().toLowerCase().replaceAll(RegExp(r'/+$'), '');
    return bare(a) == bare(b);
  }
  return left.scheme.toLowerCase() == right.scheme.toLowerCase() &&
      left.host.toLowerCase() == right.host.toLowerCase() &&
      left.port == right.port;
}

final hostdControlClientProvider = Provider<HostdControlClient>((ref) {
  final client = HostdControlClient();
  ref.onDispose(client.close);
  return client;
});

final hostdSupervisorProvider = Provider<HostdSupervisor>((ref) {
  final supervisor = HostdSupervisor();
  ref.onDispose(supervisor.dispose);
  return supervisor;
});

/// What the supervisor is doing with the daemon it started: running, restarting after a crash,
/// or given up (M8.8). `/status` polling cannot tell those apart -- a daemon between restarts and
/// one that will never come back both just fail to answer -- so the screens read this alongside
/// [hostdStatusProvider].
final hostdSupervisorStateProvider = StreamProvider<HostdSupervisorState>((ref) async* {
  final supervisor = ref.watch(hostdSupervisorProvider);
  yield supervisor.state;
  yield* supervisor.states;
});

/// Polls `GET /status` every few seconds while something is watching it (the daemon screen) --
/// cheap enough locally that this doesn't need anything smarter, and stops entirely
/// (`.autoDispose`) once nobody's looking, rather than polling forever in the background.
final hostdStatusProvider = StreamProvider.autoDispose<HostdStatusSnapshot>((ref) async* {
  final client = ref.watch(hostdControlClientProvider);
  while (true) {
    yield await _fetchStatus(client);
    await Future<void>.delayed(const Duration(seconds: 3));
  }
});

/// Parses `GET /status`'s JSON body into a snapshot -- pulled out of [_fetchStatus] so it's
/// testable against a plain map, without needing a real or fake `HostdControlClient` (whose
/// conditional web/io export type the analyzer resolves inconsistently between this file's own
/// context and a VM-only test file's, even though both run the same io implementation).
HostdStatusSnapshot parseHostdStatusBody(Map<String, dynamic> body) {
  final roots = (body['roots'] as List? ?? const [])
      .map((r) => HostdRoot.fromJson(r as Map<String, dynamic>))
      .toList();
  return HostdStatusSnapshot(
    reachable: true,
    authenticated: body['authenticated'] as bool? ?? false,
    backendConnected: body['backend_connected'] as bool?,
    backendUrl: body['backend_url'] as String?,
    roots: roots,
  );
}

Future<HostdStatusSnapshot> _fetchStatus(HostdControlClient client) async {
  try {
    final body = await client.status();
    return parseHostdStatusBody(body);
  } on HostdControlUnavailable {
    return HostdStatusSnapshot(reachable: false);
  } on HostdControlError catch (error) {
    return HostdStatusSnapshot(reachable: false, error: error.message);
  }
}

/// D6: what kind of operation a [PendingApprovalSnapshot] represents -- mirrors
/// `approval_queue.dart`'s `OperationKind` exactly, so the client branches the same way the
/// daemon's own JSON already discriminates.
enum PendingApprovalKind { command, write }

/// A pending approval as the client sees it. For [PendingApprovalKind.command], `command` is
/// always the daemon's redacted display string
/// (`aura_hostd/lib/src/approval_queue.dart`'s `PendingApproval.displayCommand`) -- the real
/// command never leaves the daemon. For [PendingApprovalKind.write] (D6), `diffPreview` is the
/// same redacted-diff role instead, and `paths` names the files it would touch.
class PendingApprovalSnapshot {
  PendingApprovalSnapshot({
    required this.id,
    required this.kind,
    this.command,
    this.diffPreview,
    this.paths = const [],
    required this.cwd,
    required this.root,
    required this.requestedAt,
  });

  final String id;
  final PendingApprovalKind kind;
  final String? command;
  final String? diffPreview;
  final List<String> paths;
  final String cwd;
  final String root;
  final DateTime requestedAt;

  factory PendingApprovalSnapshot.fromJson(Map<String, dynamic> json) => PendingApprovalSnapshot(
        id: json['id'] as String,
        kind: json['kind'] == 'write' ? PendingApprovalKind.write : PendingApprovalKind.command,
        command: json['command'] as String?,
        diffPreview: json['diff_preview'] as String?,
        paths: (json['paths'] as List?)?.cast<String>() ?? const [],
        cwd: json['cwd'] as String,
        root: json['root'] as String,
        requestedAt: DateTime.parse(json['requested_at'] as String),
      );
}

/// Polls `GET /approvals` every 2s while something is watching it -- shorter than
/// [hostdStatusProvider]'s 3s since a human is actively blocking an agent's command here, unlike
/// daemon reachability. A pending approval is a low-frequency, human-paced signal (unlike D4's
/// genuinely high-frequency terminal byte stream, which justified a WebSocket instead), so
/// polling is the right fit -- see D5's plan design decision 7.
final pendingApprovalsProvider = StreamProvider.autoDispose<List<PendingApprovalSnapshot>>((ref) async* {
  final client = ref.watch(hostdControlClientProvider);
  while (true) {
    yield await _fetchApprovals(client);
    await Future<void>.delayed(const Duration(seconds: 2));
  }
});

Future<List<PendingApprovalSnapshot>> _fetchApprovals(HostdControlClient client) async {
  try {
    final raw = await client.listApprovals();
    return raw.map(PendingApprovalSnapshot.fromJson).toList();
  } on HostdControlUnavailable {
    return const [];
  } on HostdControlError {
    return const [];
  }
}

/// Buffers the daemon's own stdout/stderr (only populated when this app spawned it --
/// [HostdSupervisor.isSpawnedHere]) for the log panel, capped so a chatty daemon can't grow this
/// forever.
class HostdLogNotifier extends Notifier<List<String>> {
  static const _maxLines = 500;
  StreamSubscription<String>? _subscription;

  @override
  List<String> build() {
    final supervisor = ref.watch(hostdSupervisorProvider);
    _subscription?.cancel();
    _subscription = supervisor.output.listen(_append);
    ref.onDispose(() => _subscription?.cancel());
    return const [];
  }

  void _append(String line) {
    final next = [...state, line];
    state = next.length > _maxLines ? next.sublist(next.length - _maxLines) : next;
  }

  void clear() => state = const [];
}

final hostdLogProvider = NotifierProvider<HostdLogNotifier, List<String>>(HostdLogNotifier.new);

/// The path to the `aura_hostd` executable this app should spawn. A path the person saved wins;
/// otherwise the daemon a packaged build ships next to the app (`hostd_bundled_path.dart`, M8.10);
/// otherwise `null`, and the screens ask for one. Persisted the same way every other small app
/// setting is (`auraSecureStorage`); saving an empty path goes back to the bundled default.
class HostdExecutablePathNotifier extends AsyncNotifier<String?> {
  static const _key = 'hostd_executable_path';

  @override
  Future<String?> build() async {
    String? saved;
    try {
      saved = await auraSecureStorage.read(key: _key);
    } on Object {
      saved = null;
    }
    if (saved != null && saved.isNotEmpty) return saved;
    return bundledHostdPath();
  }

  Future<void> set(String path) async {
    await auraSecureStorage.write(key: _key, value: path);
    state = AsyncValue.data(path.isEmpty ? bundledHostdPath() : path);
  }
}

final hostdExecutablePathProvider =
    AsyncNotifierProvider<HostdExecutablePathNotifier, String?>(HostdExecutablePathNotifier.new);
