/// Diagnostics state notifier for Desktop IDE's "Problems" tab (D7 Part A.2).
///
/// Scans on workspace open, on every `FileSaved` (debounced), and on manual rerun -- never
/// continuously while typing (a disclosed cut, see `diagnostics_service.dart`'s doc comment).
/// Diffs each scan against the previous per-file map and publishes `DiagnosticAdded`/
/// `DiagnosticCleared` on the shared `IdeEventBus` -- their first real producer.
library;

import 'dart:async';

import 'package:flutter_riverpod/legacy.dart';

import 'package:aura_client/core/hostd/hostd_control_client.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart';
import 'package:aura_client/desktop/diagnostics/diagnostics_parser.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/l10n/l10n.dart';

class DiagnosticsState {
  const DiagnosticsState({
    this.tool,
    this.detected = false,
    this.scanning = false,
    this.byFile = const {},
    this.error,
  });

  /// The detected linter, or `null` if none matched -- distinguish from [detected] being false
  /// (detection hasn't run yet) so the UI can show "no tool detected" only once detection has
  /// actually completed, never as a flash before the first check runs.
  final String? tool;
  final bool detected;
  final bool scanning;
  final DiagnosticsByFile byFile;
  final String? error;

  int get totalCount => byFile.values.fold(0, (sum, list) => sum + list.length);

  DiagnosticsState copyWith({
    String? tool,
    bool? detected,
    bool? scanning,
    DiagnosticsByFile? byFile,
    String? error,
    bool clearError = false,
  }) {
    return DiagnosticsState(
      tool: tool ?? this.tool,
      detected: detected ?? this.detected,
      scanning: scanning ?? this.scanning,
      byFile: byFile ?? this.byFile,
      error: clearError ? null : (error ?? this.error),
    );
  }
}

class DiagnosticsNotifier extends StateNotifier<DiagnosticsState> {
  DiagnosticsNotifier(this._eventBus, this._hostdClient) : super(const DiagnosticsState()) {
    _eventSub = _eventBus.stream.listen((event) {
      if (event is FileSaved && event.workspaceId == _workspaceId) {
        _scheduleScan();
      }
    });
  }

  final IdeEventBus _eventBus;
  final HostdControlClient _hostdClient;
  StreamSubscription? _eventSub;
  Timer? _debounce;

  String? _rootPath;
  String? _workspaceId;

  /// Called once per workspace activation (mirrors `GitNotifier.refresh`'s explicit-trigger
  /// pattern rather than self-subscribing to `WorkspaceOpened`, since that event carries only a
  /// workspace id, not the root path this notifier needs). A no-op if already open for the same
  /// workspace, so the Problems panel can call this on every build without re-scanning.
  Future<void> open(String workspaceId, String rootPath) async {
    if (_workspaceId == workspaceId && _rootPath == rootPath) return;
    _workspaceId = workspaceId;
    _rootPath = rootPath;
    state = const DiagnosticsState();
    await _detectAndScan();
  }

  Future<void> _detectAndScan() async {
    final root = _rootPath;
    if (root == null) return;
    try {
      final result = await _hostdClient.diagnosticsDetect(root: root);
      final tool = result['tool'] as String?;
      state = state.copyWith(tool: tool, detected: true, clearError: true);
      if (tool != null) await rescan();
    } on HostdControlError catch (error) {
      state = state.copyWith(detected: true, error: error.message);
    } on HostdControlUnavailable catch (error) {
      state = state.copyWith(detected: true, error: error.message);
    }
  }

  void _scheduleScan() {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 1500), rescan);
  }

  Future<void> rescan() async {
    final root = _rootPath;
    final tool = state.tool;
    if (root == null || tool == null) return;
    state = state.copyWith(scanning: true, clearError: true);
    try {
      final result = await _hostdClient.diagnosticsRun(root: root, tool: tool);
      if (result['not_found'] == true) {
        state = state.copyWith(scanning: false, error: currentL10n.diagnosticsToolNotFound(tool));
        return;
      }
      final newByFile = parseDiagnostics(tool, result['stdout'] as String? ?? '', root);
      _publishDiffEvents(newByFile);
      state = state.copyWith(scanning: false, byFile: newByFile, clearError: true);
    } on HostdControlError catch (error) {
      state = state.copyWith(scanning: false, error: error.message);
    } on HostdControlUnavailable catch (error) {
      state = state.copyWith(scanning: false, error: error.message);
    }
  }

  void _publishDiffEvents(DiagnosticsByFile newByFile) {
    final workspaceId = _workspaceId;
    if (workspaceId == null) return;
    final allPaths = {...state.byFile.keys, ...newByFile.keys};
    for (final path in allPaths) {
      final oldList = state.byFile[path] ?? const [];
      final newList = newByFile[path] ?? const [];
      if (_sameDiagnostics(oldList, newList)) continue;
      if (newList.isEmpty) {
        _eventBus.publish(DiagnosticCleared(path, workspaceId));
      } else {
        _eventBus.publish(DiagnosticAdded(path, newList, workspaceId));
      }
    }
  }

  bool _sameDiagnostics(List<Diagnostic> a, List<Diagnostic> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i].message != b[i].message ||
          a[i].range.startLine != b[i].range.startLine ||
          a[i].range.startColumn != b[i].range.startColumn) {
        return false;
      }
    }
    return true;
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _eventSub?.cancel();
    super.dispose();
  }
}

final diagnosticsNotifierProvider = StateNotifierProvider<DiagnosticsNotifier, DiagnosticsState>((ref) {
  return DiagnosticsNotifier(ref.watch(ideEventBusProvider), ref.watch(hostdControlClientProvider));
});
