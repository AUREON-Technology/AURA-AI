/// Debug session state notifier for Desktop IDE's Debug Console (D7 Part A.3).
///
/// Drives the standard DAP "attach" handshake against debugpy (matching the
/// `--listen ... --wait-for-client` invocation `debug_session_manager.dart` spawns on the daemon
/// side): `initialize` -> `attach` -> wait for the `initialized` event -> `setBreakpoints` per
/// source -> `configurationDone`, then reacts to `stopped`/`output`/`terminated` events for the
/// rest of the session. One active session per workspace, mirroring `TerminalNotifier`'s scope --
/// the Debug Console is a single tab, not a multi-session tree.
library;

import 'dart:async';

import 'package:flutter_riverpod/legacy.dart';
import 'package:path/path.dart' as p;

import 'package:aura_client/core/hostd/hostd_debug_client.dart';
import 'package:aura_client/desktop/debug/dap_client.dart';

enum DebugStatus { idle, starting, running, paused, stopped }

class DebugState {
  const DebugState({
    this.status = DebugStatus.idle,
    this.pid,
    this.scriptPath,
    this.consoleOutput = '',
    this.breakpoints = const {},
    this.stackFrames = const [],
    this.currentThreadId,
    this.variablesByScope = const {},
    this.error,
  });

  final DebugStatus status;
  final int? pid;
  final String? scriptPath;
  final String consoleOutput;

  /// Relative file path -> set of 1-based breakpoint lines. Settable regardless of whether a
  /// session is running (a disclosed simplification for this first cut: there is no editor
  /// gutter-click UI yet -- see D7 plan Part A.3's own note on `flutter_code_editor`'s gutter not
  /// exposing a tap hook -- so breakpoints are set through this notifier directly).
  final Map<String, Set<int>> breakpoints;
  final List<Map<String, dynamic>> stackFrames;
  final int? currentThreadId;
  final Map<String, List<Map<String, dynamic>>> variablesByScope;
  final String? error;

  bool get isActive => status == DebugStatus.running || status == DebugStatus.paused || status == DebugStatus.starting;

  DebugState copyWith({
    DebugStatus? status,
    int? pid,
    String? scriptPath,
    String? consoleOutput,
    Map<String, Set<int>>? breakpoints,
    List<Map<String, dynamic>>? stackFrames,
    int? currentThreadId,
    bool clearCurrentThreadId = false,
    Map<String, List<Map<String, dynamic>>>? variablesByScope,
    String? error,
    bool clearError = false,
  }) {
    return DebugState(
      status: status ?? this.status,
      pid: pid ?? this.pid,
      scriptPath: scriptPath ?? this.scriptPath,
      consoleOutput: consoleOutput ?? this.consoleOutput,
      breakpoints: breakpoints ?? this.breakpoints,
      stackFrames: stackFrames ?? this.stackFrames,
      currentThreadId: clearCurrentThreadId ? null : (currentThreadId ?? this.currentThreadId),
      variablesByScope: variablesByScope ?? this.variablesByScope,
      error: clearError ? null : (error ?? this.error),
    );
  }
}

class DebugNotifier extends StateNotifier<DebugState> {
  DebugNotifier() : super(const DebugState());

  HostdDebugClient? _transport;
  DapClient? _dap;
  StreamSubscription<Map<String, dynamic>>? _eventsSub;
  StreamSubscription<String>? _errorsSub;
  StreamSubscription<int>? _exitSub;
  String? _rootPath;

  Future<void> start({required String rootPath, required String scriptPath}) async {
    if (state.isActive) return;
    _rootPath = rootPath;
    state = state.copyWith(status: DebugStatus.starting, scriptPath: scriptPath, consoleOutput: '', clearError: true);

    final transport = HostdDebugClient();
    _transport = transport;
    _errorsSub = transport.errors.listen((message) {
      state = state.copyWith(status: DebugStatus.stopped, error: message);
    });
    _exitSub = transport.processExitCode.listen((_) {
      state = state.copyWith(status: DebugStatus.stopped);
    });

    try {
      await transport.connect(rootPath: rootPath, scriptPath: scriptPath);
    } catch (error) {
      state = state.copyWith(status: DebugStatus.stopped, error: '$error');
      return;
    }

    final dap = DapClient(transport);
    _dap = dap;
    final initialized = Completer<void>();
    _eventsSub = dap.events.listen((event) => _handleEvent(event, initialized));

    try {
      await dap.sendRequest('initialize', {
        'clientID': 'aura',
        'adapterID': 'debugpy',
        'linesStartAt1': true,
        'columnsStartAt1': true,
        'pathFormat': 'path',
      });
      unawaited(dap.sendRequest('attach', {'justMyCode': false}));
      await initialized.future.timeout(const Duration(seconds: 10));

      for (final entry in state.breakpoints.entries) {
        if (entry.value.isEmpty) continue;
        await dap.sendRequest('setBreakpoints', {
          'source': {'path': p.join(rootPath, entry.key)},
          'breakpoints': entry.value.map((line) => {'line': line}).toList(),
        });
      }
      await dap.sendRequest('configurationDone');
      state = state.copyWith(status: DebugStatus.running, pid: transport.pid);
    } catch (error) {
      state = state.copyWith(status: DebugStatus.stopped, error: '$error');
    }
  }

  void _handleEvent(Map<String, dynamic> event, Completer<void> initialized) {
    final body = event['body'] as Map<String, dynamic>?;
    switch (event['event'] as String?) {
      case 'initialized':
        if (!initialized.isCompleted) initialized.complete();
      case 'stopped':
        unawaited(_handleStopped(body));
      case 'continued':
        state = state.copyWith(status: DebugStatus.running, stackFrames: const [], variablesByScope: const {});
      case 'output':
        final output = body?['output'] as String?;
        if (output != null) state = state.copyWith(consoleOutput: state.consoleOutput + output);
      case 'terminated':
      case 'exited':
        state = state.copyWith(status: DebugStatus.stopped);
    }
  }

  Future<void> _handleStopped(Map<String, dynamic>? body) async {
    final dap = _dap;
    if (dap == null) return;
    final threadId = (body?['threadId'] as num?)?.toInt();
    state = state.copyWith(status: DebugStatus.paused, currentThreadId: threadId, clearCurrentThreadId: threadId == null);
    if (threadId == null) return;
    try {
      final stackTraceResp = await dap.sendRequest('stackTrace', {'threadId': threadId});
      final frames =
          ((stackTraceResp['body'] as Map<String, dynamic>?)?['stackFrames'] as List? ?? const [])
              .cast<Map<String, dynamic>>();
      state = state.copyWith(stackFrames: frames);
      if (frames.isEmpty) return;

      final topFrameId = (frames.first['id'] as num).toInt();
      final scopesResp = await dap.sendRequest('scopes', {'frameId': topFrameId});
      final scopes = ((scopesResp['body'] as Map<String, dynamic>?)?['scopes'] as List? ?? const [])
          .cast<Map<String, dynamic>>();

      final variablesByScope = <String, List<Map<String, dynamic>>>{};
      for (final scope in scopes) {
        final ref = (scope['variablesReference'] as num?)?.toInt() ?? 0;
        if (ref == 0) continue;
        final varsResp = await dap.sendRequest('variables', {'variablesReference': ref});
        variablesByScope[scope['name'] as String? ?? 'Variables'] =
            ((varsResp['body'] as Map<String, dynamic>?)?['variables'] as List? ?? const [])
                .cast<Map<String, dynamic>>();
      }
      state = state.copyWith(variablesByScope: variablesByScope);
    } catch (_) {
      // A stopped-event follow-up query failing (adapter torn down mid-query, etc.) shouldn't
      // crash the session -- the user still sees "paused," just without stack/variable detail.
    }
  }

  Future<void> _sendControl(String command) async {
    final dap = _dap;
    final threadId = state.currentThreadId;
    if (dap == null || threadId == null) return;
    try {
      await dap.sendRequest(command, {'threadId': threadId});
    } catch (_) {
      // Best-effort -- a failed step/continue leaves state as-is rather than throwing through a
      // button's onPressed handler.
    }
  }

  Future<void> continue_() => _sendControl('continue');
  Future<void> stepOver() => _sendControl('next');
  Future<void> stepIn() => _sendControl('stepIn');
  Future<void> stepOut() => _sendControl('stepOut');

  Future<void> stop() async {
    final dap = _dap;
    try {
      await dap?.sendRequest('disconnect', {'terminateDebuggee': true});
    } catch (_) {
      // The debuggee may already be gone -- disconnect failing is not itself an error to surface.
    }
    await _eventsSub?.cancel();
    await _errorsSub?.cancel();
    await _exitSub?.cancel();
    await dap?.dispose();
    await _transport?.dispose();
    _dap = null;
    _transport = null;
    state = state.copyWith(status: DebugStatus.stopped, clearCurrentThreadId: true, stackFrames: const [], variablesByScope: const {});
  }

  /// Toggles a breakpoint at [line] (1-based) in [path] (workspace-relative, matching
  /// `EditorTab.path`). Applies immediately to a running session via a live `setBreakpoints`
  /// call -- DAP supports changing breakpoints at any time, not just before `configurationDone`.
  void toggleBreakpoint(String path, int line) {
    final lines = Set<int>.from(state.breakpoints[path] ?? const <int>{});
    if (!lines.remove(line)) lines.add(line);
    final updated = Map<String, Set<int>>.from(state.breakpoints);
    if (lines.isEmpty) {
      updated.remove(path);
    } else {
      updated[path] = lines;
    }
    state = state.copyWith(breakpoints: updated);

    final dap = _dap;
    final rootPath = _rootPath;
    if (dap != null && rootPath != null) {
      unawaited(dap.sendRequest('setBreakpoints', {
        'source': {'path': p.join(rootPath, path)},
        'breakpoints': lines.map((l) => {'line': l}).toList(),
      }));
    }
  }

  @override
  void dispose() {
    unawaited(stop());
    super.dispose();
  }
}

final debugNotifierProvider = StateNotifierProvider<DebugNotifier, DebugState>((ref) => DebugNotifier());
