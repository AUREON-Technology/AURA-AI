/// Desktop IDE shell layout.
///
/// Main layout for the Windows IDE: ActivityBar | PrimarySidebar | MainWorkspace | SecondarySidebar
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/shell/activity_bar.dart';
import 'package:aura_client/desktop/shell/bottom_panel.dart';
import 'package:aura_client/desktop/shell/status_bar.dart';
import 'package:aura_client/desktop/shell/desktop_shortcuts.dart';
import 'package:aura_client/desktop/agent/agent_panel.dart';
import 'package:aura_client/desktop/explorer/explorer_panel.dart';
import 'package:aura_client/desktop/git/git_panel.dart';
import 'package:aura_client/desktop/editor/editor_tab_bar.dart';
import 'package:aura_client/desktop/editor/code_editor_view.dart';
import 'package:aura_client/desktop/editor/quick_open_dialog.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

/// [child] is the currently routed `/ide/*` page (welcome or workspace
/// manager) — shown in the main content area only while no file is open,
/// the same way an IDE shows a "Get Started" view until a tab is active.
class DesktopShell extends ConsumerStatefulWidget {
  const DesktopShell({super.key, required this.child});

  final Widget child;

  @override
  ConsumerState<DesktopShell> createState() => _DesktopShellState();
}

class _DesktopShellState extends ConsumerState<DesktopShell> {
  StreamSubscription<IdeEvent>? _eventSub;

  @override
  void initState() {
    super.initState();
    _eventSub = ref.read(ideEventBusProvider).stream.listen((event) {
      if (event is ShowQuickOpenRequested) _showQuickOpen();
    });
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    super.dispose();
  }

  void _showQuickOpen() {
    final root = ref.read(workspaceNotifierProvider).activeWorkspace?.rootPath;
    if (root == null) return;
    showDialog<void>(
      context: context,
      builder: (context) => QuickOpenDialog(
        workspaceRoot: root,
        onOpenFile: (relativePath) {
          final workspaceId = ref.read(workspaceNotifierProvider).activeWorkspaceId ?? '';
          ref.read(editorNotifierProvider.notifier).openTab(
                tabId: relativePath,
                path: relativePath,
                workspaceId: workspaceId,
              );
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final ideDensity = context.ideDensity;
    final palette = AuraPalette.of(context);
    final layoutState = ref.watch(layoutNotifierProvider);
    final hasActiveTab = ref.watch(editorNotifierProvider).activeTab != null;

    return DesktopShortcuts(
      child: Scaffold(
        body: Column(
          children: [
            // Activity bar + primary sidebar + main workspace + secondary sidebar
            Expanded(
              child: Row(
                children: [
                  // Activity bar
                  SizedBox(
                    width: ideDensity.activityBarWidth,
                    child: const ActivityBar(),
                  ),
                  // Primary sidebar (Explorer or Source Control, per D5)
                  if (layoutState.showPrimarySidebar)
                    SizedBox(
                      width: layoutState.primarySidebarWidth,
                      child: switch (layoutState.primarySidebarPanel) {
                        PrimarySidebarPanel.explorer => const ExplorerPanel(),
                        PrimarySidebarPanel.sourceControl => const GitPanel(),
                      },
                    ),
                  if (layoutState.showPrimarySidebar)
                    VerticalDivider(
                      thickness: 1,
                      width: 1,
                      color: palette.border,
                    ),
                  // Main workspace area
                  Expanded(
                    child: Column(
                      children: [
                        Expanded(
                          child: hasActiveTab
                              ? Column(
                                  children: [
                                    const EditorTabBar(),
                                    const Expanded(child: CodeEditorView()),
                                  ],
                                )
                              : widget.child,
                        ),
                        if (layoutState.showBottomPanel)
                          SizedBox(
                            height: layoutState.bottomPanelHeight,
                            child: const BottomPanel(),
                          ),
                      ],
                    ),
                  ),
                  // Secondary sidebar
                  if (layoutState.showSecondarySidebar)
                    VerticalDivider(
                      thickness: 1,
                      width: 1,
                      color: palette.border,
                    ),
                  if (layoutState.showSecondarySidebar)
                    SizedBox(
                      width: layoutState.secondarySidebarWidth,
                      child: const AgentPanel(),
                    ),
                ],
              ),
            ),
            // Status bar
            SizedBox(
              height: ideDensity.statusBarHeight,
              child: const StatusBar(),
            ),
          ],
        ),
      ),
    );
  }
}
