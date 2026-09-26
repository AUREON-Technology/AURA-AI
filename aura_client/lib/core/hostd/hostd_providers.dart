/// Riverpod wiring for the desktop Engineer daemon screen (ADR-011 Unit 8): the control client,
/// the process supervisor, a polled status snapshot, and the executable-path setting a person
/// configures once -- there is no bundled `aura_hostd` binary to default to, unlike
/// `google_desktop_client_id` on the backend, which is a registered OAuth client id now
/// hardcoded rather than left for an operator to supply.
library;

import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../security/aura_secure_storage.dart';
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
    this.backendConnected = false,
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
  final bool backendConnected;
  final String? backendUrl;
  final List<HostdRoot> roots;
  final String? error;
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
    backendConnected: body['backend_connected'] as bool? ?? false,
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

/// The path to the `aura_hostd` executable this app should spawn -- empty until a person
/// configures it (no bundled binary shipped, per ADR-011's disclosed scope). Persisted the same
/// way every other small app setting is (`auraSecureStorage`).
class HostdExecutablePathNotifier extends AsyncNotifier<String?> {
  static const _key = 'hostd_executable_path';

  @override
  Future<String?> build() async {
    try {
      final value = await auraSecureStorage.read(key: _key);
      return (value == null || value.isEmpty) ? null : value;
    } on Object {
      return null;
    }
  }

  Future<void> set(String path) async {
    await auraSecureStorage.write(key: _key, value: path);
    state = AsyncValue.data(path);
  }
}

final hostdExecutablePathProvider =
    AsyncNotifierProvider<HostdExecutablePathNotifier, String?>(HostdExecutablePathNotifier.new);
