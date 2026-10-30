/// Debug Console panel widget for Desktop IDE's bottom-panel "Debug Console" tab (D7 Part A.3).
///
/// Breakpoints are managed through the small list in this panel rather than an editor gutter
/// click -- `flutter_code_editor` (the installed version, 0.3.5) declares a `lineNumberBuilder`
/// field on `CodeField` but never actually wires it into `GutterWidget`'s rendering, and exposes
/// no external scroll controller either, so there is no reliable hook from outside the package to
/// intercept a tap on a specific gutter line. `DebugNotifier.toggleBreakpoint` itself is fully
/// functional (applies live to a running session via DAP's `setBreakpoints`) -- only the
/// click-in-the-gutter *input method* is deferred, not breakpoint support itself.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/debug/debug_notifier.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

class DebugConsoleView extends ConsumerStatefulWidget {
  const DebugConsoleView({super.key});

  @override
  ConsumerState<DebugConsoleView> createState() => _DebugConsoleViewState();
}

class _DebugConsoleViewState extends ConsumerState<DebugConsoleView> {
  final _scriptController = TextEditingController();
  final _breakpointLineController = TextEditingController();
  final _consoleScrollController = ScrollController();

  @override
  void dispose() {
    _scriptController.dispose();
    _breakpointLineController.dispose();
    _consoleScrollController.dispose();
    super.dispose();
  }

  void _scrollConsoleToBottom() {
    if (!_consoleScrollController.hasClients) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_consoleScrollController.hasClients) return;
      _consoleScrollController.jumpTo(_consoleScrollController.position.maxScrollExtent);
    });
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final workspace = ref.watch(workspaceNotifierProvider).activeWorkspace;
    final debug = ref.watch(debugNotifierProvider);
    final notifier = ref.read(debugNotifierProvider.notifier);

    ref.listen(debugNotifierProvider, (previous, next) {
      if (next.consoleOutput != previous?.consoleOutput) _scrollConsoleToBottom();
    });

    if (workspace == null) {
      return _Centered(text: 'Open a workspace to use the Debug Console.', palette: palette);
    }

    if (_scriptController.text.isEmpty) {
      final activeTab = ref.read(editorNotifierProvider).activeTab;
      if (activeTab != null) _scriptController.text = activeTab.path;
    }

    return Container(
      color: palette.surface,
      child: Column(
        children: [
          _buildToolbar(palette, workspace.rootPath, debug, notifier),
          if (debug.error != null)
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: AuraSpacing.xs),
              color: palette.danger.withValues(alpha: 0.1),
              child: Text(debug.error!, style: TextStyle(color: palette.danger, fontSize: 12)),
            ),
          _buildBreakpointsBar(palette, notifier),
          Expanded(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Expanded(
                  flex: 2,
                  child: SingleChildScrollView(
                    controller: _consoleScrollController,
                    padding: const EdgeInsets.all(AuraSpacing.sm),
                    child: SelectableText(
                      debug.consoleOutput.isEmpty ? ' ' : debug.consoleOutput,
                      style: TextStyle(fontFamily: 'Courier', fontSize: 12, color: palette.textPrimary, height: 1.4),
                    ),
                  ),
                ),
                VerticalDivider(width: 1, color: palette.border),
                Expanded(
                  child: _buildSidePanel(palette, debug),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildToolbar(AuraPalette palette, String rootPath, DebugState debug, DebugNotifier notifier) {
    final isIdleOrStopped = debug.status == DebugStatus.idle || debug.status == DebugStatus.stopped;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: AuraSpacing.xs),
      decoration: BoxDecoration(border: Border(bottom: BorderSide(color: palette.border))),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: _scriptController,
              enabled: isIdleOrStopped,
              decoration: const InputDecoration(
                isDense: true,
                border: InputBorder.none,
                hintText: 'script.py (relative to workspace root)',
              ),
              style: TextStyle(fontFamily: 'Courier', fontSize: 12, color: palette.textPrimary),
            ),
          ),
          if (isIdleOrStopped)
            IconButton(
              icon: Icon(Icons.play_arrow, size: 18, color: palette.success),
              tooltip: 'Start',
              onPressed: _scriptController.text.trim().isEmpty
                  ? null
                  : () => notifier.start(rootPath: rootPath, scriptPath: _scriptController.text.trim()),
            )
          else ...[
            IconButton(
              icon: Icon(Icons.play_arrow, size: 18, color: palette.textSecondary),
              tooltip: 'Continue',
              onPressed: debug.status == DebugStatus.paused ? notifier.continue_ : null,
            ),
            IconButton(
              icon: Icon(Icons.arrow_downward, size: 18, color: palette.textSecondary),
              tooltip: 'Step Over',
              onPressed: debug.status == DebugStatus.paused ? notifier.stepOver : null,
            ),
            IconButton(
              icon: Icon(Icons.south_east, size: 18, color: palette.textSecondary),
              tooltip: 'Step Into',
              onPressed: debug.status == DebugStatus.paused ? notifier.stepIn : null,
            ),
            IconButton(
              icon: Icon(Icons.north_east, size: 18, color: palette.textSecondary),
              tooltip: 'Step Out',
              onPressed: debug.status == DebugStatus.paused ? notifier.stepOut : null,
            ),
            IconButton(
              icon: Icon(Icons.stop, size: 18, color: palette.danger),
              tooltip: 'Stop',
              onPressed: notifier.stop,
            ),
          ],
          Padding(
            padding: const EdgeInsetsDirectional.only(start: AuraSpacing.sm),
            child: Text(_statusLabel(debug), style: TextStyle(fontSize: 11, color: palette.textDisabled)),
          ),
        ],
      ),
    );
  }

  String _statusLabel(DebugState debug) => switch (debug.status) {
        DebugStatus.idle => 'Idle',
        DebugStatus.starting => 'Starting...',
        DebugStatus.running => debug.pid != null ? 'Running (pid ${debug.pid})' : 'Running',
        DebugStatus.paused => 'Paused',
        DebugStatus.stopped => 'Stopped',
      };

  Widget _buildBreakpointsBar(AuraPalette palette, DebugNotifier notifier) {
    final path = _scriptController.text.trim();
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: 2),
      decoration: BoxDecoration(border: Border(bottom: BorderSide(color: palette.border))),
      child: Row(
        children: [
          Icon(Icons.circle, size: 8, color: palette.danger),
          const SizedBox(width: AuraSpacing.xs),
          Text('Breakpoints:', style: TextStyle(fontSize: 11, color: palette.textSecondary)),
          const SizedBox(width: AuraSpacing.xs),
          SizedBox(
            width: 60,
            child: TextField(
              controller: _breakpointLineController,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(isDense: true, hintText: 'line #', border: InputBorder.none),
              style: const TextStyle(fontSize: 12, fontFamily: 'Courier'),
              onSubmitted: (value) {
                final line = int.tryParse(value.trim());
                if (line != null && path.isNotEmpty) notifier.toggleBreakpoint(path, line);
                _breakpointLineController.clear();
              },
            ),
          ),
          const SizedBox(width: AuraSpacing.sm),
          Expanded(
            child: Consumer(
              builder: (context, ref, _) {
                final breakpoints = ref.watch(debugNotifierProvider.select((s) => s.breakpoints));
                final entries = <Widget>[];
                for (final entry in breakpoints.entries) {
                  for (final line in entry.value) {
                    entries.add(InputChip(
                      label: Text('${entry.key}:$line', style: const TextStyle(fontSize: 11)),
                      onDeleted: () => notifier.toggleBreakpoint(entry.key, line),
                      visualDensity: VisualDensity.compact,
                    ));
                  }
                }
                return Wrap(spacing: 4, children: entries);
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildSidePanel(AuraPalette palette, DebugState debug) {
    return ListView(
      padding: const EdgeInsets.all(AuraSpacing.sm),
      children: [
        Text('CALL STACK', style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700, color: palette.textDisabled)),
        const SizedBox(height: AuraSpacing.xs),
        if (debug.stackFrames.isEmpty)
          Text('--', style: TextStyle(fontSize: 12, color: palette.textDisabled))
        else
          for (final frame in debug.stackFrames)
            Padding(
              padding: const EdgeInsets.only(bottom: 2),
              child: Text(
                '${frame['name'] ?? '?'}  ${((frame['source'] as Map?)?['name']) ?? ''}:${frame['line']}',
                style: TextStyle(fontSize: 11, color: palette.textPrimary, fontFamily: 'Courier'),
              ),
            ),
        const SizedBox(height: AuraSpacing.md),
        Text('VARIABLES', style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700, color: palette.textDisabled)),
        const SizedBox(height: AuraSpacing.xs),
        if (debug.variablesByScope.isEmpty)
          Text('--', style: TextStyle(fontSize: 12, color: palette.textDisabled))
        else
          for (final scopeEntry in debug.variablesByScope.entries) ...[
            Text(scopeEntry.key, style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: palette.textSecondary)),
            for (final variable in scopeEntry.value)
              Padding(
                padding: const EdgeInsetsDirectional.only(start: AuraSpacing.sm, bottom: 1),
                child: Text(
                  '${variable['name']} = ${variable['value']}',
                  style: TextStyle(fontSize: 11, color: palette.textPrimary, fontFamily: 'Courier'),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            const SizedBox(height: AuraSpacing.xs),
          ],
      ],
    );
  }
}

class _Centered extends StatelessWidget {
  const _Centered({required this.text, required this.palette});

  final String text;
  final AuraPalette palette;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Text(text, style: TextStyle(color: palette.textSecondary, fontSize: 12)),
    );
  }
}
