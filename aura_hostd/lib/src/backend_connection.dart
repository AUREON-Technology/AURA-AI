/// The daemon's outbound connection to the backend (`HostAgentClient`, `rpc_client.dart`) and
/// the `TaskHandler` behind it, owned in one place so the "already paired at startup" and
/// "paired later through the control server" paths share it -- and so tests can drive exactly
/// the wiring `aura_hostd run` uses (moved out of `bin/aura_hostd.dart` for that reason).
library;

import 'dart:async';
import 'dart:io';

import 'approval_queue.dart';
import 'config.dart';
import 'isolated_task_registry.dart';
import 'rpc_client.dart';
import 'task_handler.dart';
import 'workspace_store.dart';

typedef HostAgentClientFactory = HostAgentClient Function({
  required String wsUrl,
  required String authToken,
  required TaskHandler taskHandler,
});

class BackendConnection {
  BackendConnection({
    required this.configStore,
    required this.workspaceStore,
    required this.approvalQueue,
    required this.isolatedTasks,
    HostAgentClientFactory? clientFactory,
    void Function(String message)? logger,
  })  : _clientFactory = clientFactory ?? _defaultClientFactory,
        _logger = logger ?? ((message) => stdout.writeln(message));

  final ConfigStore configStore;

  /// Its [WorkspaceStore.live] roots are what every agent RPC resolves against -- the same
  /// instance the control server's root actions update (audit INT-002).
  final WorkspaceStore workspaceStore;

  /// The same queue instance `ControlServer`'s `/approvals` routes act on.
  final ApprovalQueue approvalQueue;

  /// The same instance `ControlServer` holds, built on `workspaceStore.live`.
  final IsolatedTaskRegistry isolatedTasks;

  final HostAgentClientFactory _clientFactory;
  final void Function(String message) _logger;
  HostAgentClient? _client;

  static HostAgentClient _defaultClientFactory({
    required String wsUrl,
    required String authToken,
    required TaskHandler taskHandler,
  }) =>
      HostAgentClient(wsUrl: wsUrl, authToken: authToken, taskHandler: taskHandler);

  /// `false` both before pairing and while a post-drop reconnect is in backoff.
  bool get isConnected => _client?.isConnected ?? false;

  /// The backend refused the stored token (close 4401) and the daemon stopped trying (audit
  /// INT-003). Stays true until a new pairing starts a fresh connection.
  bool get credentialsRejected => _client?.credentialsRejected ?? false;

  int? get lastCloseCode => _client?.lastCloseCode;

  /// Starts the connection if a token is stored and no live client is running. A client that
  /// has finished -- the server rejected its token -- does not count as running, so a re-pair
  /// after a 4401 connects again instead of being ignored (INT-003).
  void startIfAuthenticated() {
    final existing = _client;
    if (existing != null && !existing.isFinished) return;
    final config = configStore.load();
    final token = config.authToken;
    if (token == null || token.isEmpty) return;
    final String wsUrl;
    try {
      wsUrl = config.hostAgentWsUrl;
    } on InvalidBackendUrlError catch (error) {
      // F53: never send the token in clear; pairing again with an https URL fixes it.
      _logger('Not connecting: ${error.message}');
      return;
    }
    final handler = TaskHandler.live(
      workspaceStore,
      approvalQueue: approvalQueue,
      isolatedTasks: isolatedTasks,
      // WP8: read from config.json on every call of a remote task, so the desktop's
      // "Allow control from my phone and the web" switch applies at once.
      remoteControlEnabled: configStore.remoteControlEnabled,
    );
    final client = _clientFactory(
      wsUrl: wsUrl,
      authToken: token,
      taskHandler: handler,
    );
    _client = client;
    _logger('Connecting to ${config.backendUrl} ...');
    unawaited(client.run());
  }

  /// A new token was just saved (pairing completed): drop whatever connection is running with
  /// the old one and connect with the new one.
  void restart() {
    _client?.stop();
    _client = null;
    startIfAuthenticated();
  }

  void stop() => _client?.stop();

  /// The daemon is exiting: end the commands the agent started (and what they spawned) instead
  /// of orphaning them. Call after [stop].
  Future<void> stopRunningCommands() async => await _client?.stopRunningCommands();
}
