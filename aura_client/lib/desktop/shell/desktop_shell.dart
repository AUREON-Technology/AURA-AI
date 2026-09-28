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
import 'package:aura_client/desktop/shell/desktop_breakpoint.dart';
import 'package:aura_client/desktop/shell/desktop_commands.dart';
import 'package:aura_client/desktop/shell/status_bar.dart';
import 'package:aura_client/desktop/shell/desktop_shortcuts.dart';
import 'package:aura_client/desktop/shell/resize_handle.dart';
import 'package:aura_client/desktop/agent/agent_panel.dart';
import 'package:aura_client/desktop/control_center/control_center_panel.dart';
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
      if (event is ShowCommandPaletteRequested) _showCommandPalette();
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

  void _showCommandPalette() {
    showAuraCommandPalette(
      context,
      // Built at open time, not held: whether a command can run right now is
      // a snapshot of this moment, and the palette does not outlive it.
      commands: buildDesktopCommands(ref, platform: Theme.of(context).platform),
    );
  }

  @override
  Widget build(BuildContext context) {
    // Register the IDE density tokens for this subtree only (see
    // aura_ide_density.dart): the global V2 theme deliberately leaves them
    // out so mobile layouts are untouched. Built through a Map (not a list
    // literal) so every type is exact -- no generic inference is involved:
    // `Map.of` preserves the registry's own value type, and `copyWith` takes
    // exactly `Iterable<ThemeExtension<dynamic>>`.
    final appTheme = Theme.of(context);
    final Map<Object, ThemeExtension<dynamic>> ideExtensions =
        Map<Object, ThemeExtension<dynamic>>.of(appTheme.extensions);
    ideExtensions[AuraIdeDensity] = AuraIdeDensity.defaultDensity;
    final ideTheme = appTheme.copyWith(extensions: ideExtensions.values);

    // The LayoutBuilder sits outside DesktopShortcuts on purpose: the Escape
    // binding that dismisses a floating panel has to be registered at or
    // above the Focus that DesktopShortcuts autofocuses, or shortcut
    // resolution -- which walks *up* from the focused node -- never reaches
    // it. So the breakpoint has to be known before that Focus is built.
    return LayoutBuilder(
      builder: (context, constraints) {
        final breakpoint = DesktopBreakpoint.of(constraints.maxWidth);
        final layoutState = ref.watch(layoutNotifierProvider);
        final regions = ShellRegions.resolve(
          breakpoint: breakpoint,
          showPrimarySidebar: layoutState.showPrimarySidebar,
          showSecondarySidebar: layoutState.showSecondarySidebar,
          preferredPrimaryWidth: layoutState.primarySidebarWidth,
          preferredSecondaryWidth: layoutState.secondarySidebarWidth,
          availableWidth: constraints.maxWidth,
          activityBarWidth: AuraIdeDensity.defaultDensity.activityBarWidth,
        );

        return DesktopShortcuts(
          // Only bound while something is actually floating, so Escape keeps
          // its usual meaning everywhere else.
          onDismissOverlay: regions.hasFloatingPanel
              ? () => _dismissTopPanel(ref, regions)
              : null,
          child: Theme(
            data: ideTheme,
            child: Scaffold(
              body: _ShellBody(regions: regions, child: widget.child),
            ),
          ),
        );
      },
    );
  }
}

/// Closes whatever is on top: the AI panel sits over the Explorer, so it goes
/// first. Shared by the scrim and the Escape binding so the two can never
/// disagree about which panel "dismiss" means.
void _dismissTopPanel(WidgetRef ref, ShellRegions regions) {
  final notifier = ref.read(layoutNotifierProvider.notifier);
  if (regions.secondaryFloating) {
    notifier.toggleSecondarySidebar();
  } else {
    notifier.togglePrimarySidebar();
  }
}

/// The shell's own content, split out so it rebuilds against the density
/// tokens registered by the [Theme] above it rather than the app theme.
class _ShellBody extends ConsumerWidget {
  const _ShellBody({required this.regions, required this.child});

  /// Every width decision is already resolved here — see [ShellRegions]. This
  /// widget renders that answer and does no layout arithmetic of its own,
  /// which is what makes the rules testable without building the Explorer,
  /// the editor and the agent panel to look at them.
  final ShellRegions regions;
  final Widget child;

  Widget _primaryPanel(PrimarySidebarPanel panel) => switch (panel) {
        PrimarySidebarPanel.explorer => const ExplorerPanel(),
        PrimarySidebarPanel.sourceControl => const GitPanel(),
        PrimarySidebarPanel.controlCenter => const ControlCenterPanel(),
      };

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ideDensity = context.ideDensity;
    final layoutState = ref.watch(layoutNotifierProvider);
    final hasActiveTab = ref.watch(editorNotifierProvider).activeTab != null;

    final shell = Column(
      children: [
        Expanded(
          child: Row(
            children: [
              SizedBox(
                width: ideDensity.activityBarWidth,
                child: const ActivityBar(),
              ),
              if (regions.primaryDocked) ...[
                SizedBox(
                  width: regions.primaryWidth,
                  child: _primaryPanel(layoutState.primarySidebarPanel),
                ),
                ResizeHandle(
                  axis: ResizeHandleAxis.horizontal,
                  onDelta: (dx) =>
                      ref.read(layoutNotifierProvider.notifier).adjustPrimarySidebarWidth(dx),
                ),
              ],
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
                          : child,
                    ),
                    if (layoutState.showBottomPanel)
                      ResizeHandle(
                        axis: ResizeHandleAxis.vertical,
                        // The handle sits above the bottom panel, so dragging it up
                        // (negative dy) should grow the panel -- hence the negation.
                        onDelta: (dy) =>
                            ref.read(layoutNotifierProvider.notifier).adjustBottomPanelHeight(-dy),
                      ),
                    if (layoutState.showBottomPanel)
                      SizedBox(
                        height: layoutState.bottomPanelHeight,
                        child: const BottomPanel(),
                      ),
                  ],
                ),
              ),
              if (regions.secondaryDocked) ...[
                ResizeHandle(
                  axis: ResizeHandleAxis.horizontal,
                  // The handle sits to the left of the secondary sidebar, so dragging it
                  // left (negative dx) should grow the panel -- hence the negation.
                  onDelta: (dx) =>
                      ref.read(layoutNotifierProvider.notifier).adjustSecondarySidebarWidth(-dx),
                ),
                SizedBox(
                  width: regions.secondaryWidth,
                  child: const AgentPanel(),
                ),
              ],
            ],
          ),
        ),
        // Status bar
        SizedBox(
          height: ideDensity.statusBarHeight,
          child: const StatusBar(),
        ),
      ],
    );

    if (!regions.hasFloatingPanel) return shell;

    return Stack(
      children: [
        shell,
        // A floating panel covers the editor, so it gets a scrim: without one
        // there is no indication that the content behind it is not the thing
        // being interacted with, and no way to dismiss it by pointer except
        // by finding the same activity-bar item again.
        Positioned.fill(
          child: _OverlayScrim(onDismiss: () => _dismissTopPanel(ref, regions)),
        ),
        if (regions.primaryFloating && !regions.secondaryFloating)
          Positioned(
            left: ideDensity.activityBarWidth,
            top: 0,
            bottom: ideDensity.statusBarHeight,
            width: regions.primaryWidth,
            child: Material(
              elevation: AuraElevation.floating,
              child: _primaryPanel(layoutState.primarySidebarPanel),
            ),
          ),
        if (regions.secondaryFloating)
          Positioned(
            // In compact the AI panel takes the whole content area rather
            // than a sliver of it -- a chat column 200px wide is not usable.
            left: regions.secondaryFillsContentArea ? ideDensity.activityBarWidth : null,
            right: 0,
            top: 0,
            bottom: ideDensity.statusBarHeight,
            width: regions.secondaryFillsContentArea ? null : regions.secondaryWidth,
            child: Material(
              elevation: AuraElevation.floating,
              child: const AgentPanel(),
            ),
          ),
      ],
    );
  }
}

class _OverlayScrim extends StatelessWidget {
  const _OverlayScrim({required this.onDismiss});

  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: 'Close panel',
      child: GestureDetector(
        onTap: onDismiss,
        child: ColoredBox(color: Colors.black.withValues(alpha: 0.32)),
      ),
    );
  }
}
