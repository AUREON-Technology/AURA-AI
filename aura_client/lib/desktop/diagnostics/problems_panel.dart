/// Problems panel widget for Desktop IDE's bottom-panel "Problems" tab (D7 Part A.2).
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/diagnostics/diagnostics_notifier.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

class ProblemsPanel extends ConsumerStatefulWidget {
  const ProblemsPanel({super.key});

  @override
  ConsumerState<ProblemsPanel> createState() => _ProblemsPanelState();
}

class _ProblemsPanelState extends ConsumerState<ProblemsPanel> {
  String? _openedForKey;

  void _maybeOpen(String workspaceId, String rootPath) {
    final key = '$workspaceId::$rootPath';
    if (_openedForKey == key) return;
    _openedForKey = key;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) ref.read(diagnosticsNotifierProvider.notifier).open(workspaceId, rootPath);
    });
  }

  /// Opens (or activates) the file's tab, then asks the editor to reveal the diagnostic's
  /// position -- a fixed short delay covers a freshly-opened tab's async file load, per this
  /// feature's own disclosed best-effort scope (see D7 plan Part A.2).
  Future<void> _openDiagnostic(String path, Diagnostic diagnostic, String workspaceId) async {
    await ref.read(editorNotifierProvider.notifier).openTab(tabId: path, path: path, workspaceId: workspaceId);
    await Future<void>.delayed(const Duration(milliseconds: 300));
    if (!mounted) return;
    ref
        .read(ideEventBusProvider)
        .publish(RevealPositionRequested(path, diagnostic.range.startLine, diagnostic.range.startColumn));
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final workspace = ref.watch(workspaceNotifierProvider).activeWorkspace;

    if (workspace == null) {
      return _Centered(text: 'Open a workspace to see problems.', palette: palette);
    }

    _maybeOpen(workspace.id, workspace.rootPath);
    final diagnostics = ref.watch(diagnosticsNotifierProvider);

    if (!diagnostics.detected) {
      return _Centered(text: 'Detecting diagnostics tool...', palette: palette);
    }
    if (diagnostics.tool == null) {
      return _Centered(text: 'No diagnostics tool detected for this workspace.', palette: palette);
    }

    return Container(
      color: palette.surface,
      child: Column(
        children: [
          Container(
            padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: AuraSpacing.xs),
            decoration: BoxDecoration(border: Border(bottom: BorderSide(color: palette.border))),
            child: Row(
              children: [
                Text(diagnostics.tool!, style: TextStyle(fontSize: 12, color: palette.textSecondary)),
                const Spacer(),
                if (diagnostics.scanning)
                  const Padding(
                    padding: EdgeInsets.symmetric(horizontal: AuraSpacing.xs),
                    child: SizedBox(width: 12, height: 12, child: CircularProgressIndicator(strokeWidth: 2)),
                  ),
                IconButton(
                  icon: Icon(Icons.refresh, size: 16, color: palette.textSecondary),
                  tooltip: 'Rerun',
                  onPressed: () => ref.read(diagnosticsNotifierProvider.notifier).rescan(),
                ),
              ],
            ),
          ),
          if (diagnostics.error != null)
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: AuraSpacing.xs),
              color: palette.danger.withValues(alpha: 0.1),
              child: Text(diagnostics.error!, style: TextStyle(color: palette.danger, fontSize: 12)),
            ),
          Expanded(
            child: diagnostics.byFile.isEmpty
                ? Center(
                    child: Text('No problems detected', style: TextStyle(color: palette.textDisabled, fontSize: 12)),
                  )
                : ListView(
                    padding: const EdgeInsets.symmetric(vertical: AuraSpacing.xs),
                    children: [
                      for (final entry in diagnostics.byFile.entries) ...[
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: 4),
                          child: Text(
                            entry.key,
                            style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: palette.textPrimary),
                          ),
                        ),
                        for (final diagnostic in entry.value)
                          InkWell(
                            onTap: () => _openDiagnostic(entry.key, diagnostic, workspace.id),
                            child: Padding(
                              padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md, vertical: 3),
                              child: Row(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Icon(_iconFor(diagnostic.severity), size: 14, color: _colorFor(diagnostic.severity, palette)),
                                  const SizedBox(width: AuraSpacing.xs),
                                  Expanded(
                                    child: Text(
                                      diagnostic.message,
                                      style: TextStyle(fontSize: 12, color: palette.textPrimary),
                                    ),
                                  ),
                                  const SizedBox(width: AuraSpacing.xs),
                                  Text(
                                    '${diagnostic.range.startLine}:${diagnostic.range.startColumn}',
                                    style: TextStyle(fontSize: 11, color: palette.textDisabled, fontFamily: 'Courier'),
                                  ),
                                ],
                              ),
                            ),
                          ),
                      ],
                    ],
                  ),
          ),
        ],
      ),
    );
  }

  IconData _iconFor(DiagnosticSeverity severity) => switch (severity) {
        DiagnosticSeverity.error => Icons.cancel,
        DiagnosticSeverity.warning => Icons.warning_amber,
        DiagnosticSeverity.info => Icons.info_outline,
        DiagnosticSeverity.hint => Icons.lightbulb_outline,
      };

  Color _colorFor(DiagnosticSeverity severity, AuraPalette palette) => switch (severity) {
        DiagnosticSeverity.error => palette.danger,
        DiagnosticSeverity.warning => palette.warning,
        DiagnosticSeverity.info => palette.textSecondary,
        DiagnosticSeverity.hint => palette.textSecondary,
      };
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
