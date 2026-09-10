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
    this.backendUrl,
    this.roots = const [],
    this.error,
  });

  /// Whether the control server answered at all -- false means `aura_hostd run` isn't up (or
  /// this platform has no control file for this app to have found in the first place).
  final bool reachable;
  final bool authenticated;
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

Future<HostdStatusSnapshot> _fetchStatus(HostdControlClient client) async {
  try {
    final body = await client.status();
    final roots = (body['roots'] as List? ?? const [])
        .map((r) => HostdRoot.fromJson(r as Map<String, dynamic>))
        .toList();
    return HostdStatusSnapshot(
      reachable: true,
      authenticated: body['authenticated'] as bool? ?? false,
      backendUrl: body['backend_url'] as String?,
      roots: roots,
    );
  } on HostdControlUnavailable {
    return HostdStatusSnapshot(reachable: false);
  } on HostdControlError catch (error) {
    return HostdStatusSnapshot(reachable: false, error: error.message);
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
