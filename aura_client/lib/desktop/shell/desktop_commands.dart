/// The Desktop IDE's command set, for the command palette.
///
/// UX_BASELINE_AUDIT.md F-07: the desktop had Quick Open (files only) and nine
/// keyboard shortcuts with no discoverable list, so every action that was not
/// a visible button in some panel was unreachable unless you already knew it
/// existed. This is the list that fixes that.
///
/// Two rules hold for everything registered here:
///
/// 1. **A command that cannot run right now is listed with its reason, not
///    hidden.** A palette that quietly omits "Go to File" when no workspace is
///    open teaches the user it does not exist; one that greys it out with "No
///    workspace is open" teaches them what to do first.
/// 2. **Every command carries its keyboard shortcut** where it has one. The
///    palette is where somebody learns the shortcut for the thing they just
///    did the slow way.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/components/aura_command_palette.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/shell/desktop_actions.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

/// Platform-appropriate modifier name for the shortcut hints.
String modifierLabel(TargetPlatform platform) =>
    platform == TargetPlatform.macOS ? 'Cmd' : 'Ctrl';

/// Builds the palette's contents against current state.
///
/// Rebuilt per open rather than held as a provider: a command's *enabled*
/// state is a snapshot of the moment the palette was summoned, and the palette
/// does not outlive that moment.
///
/// The Windows app is the IDE, so there is no "Leave IDE" command: there is
/// no other screen to leave to.
List<AuraCommandItem> buildDesktopCommands(
  WidgetRef ref, {
  required TargetPlatform platform,
}) {
  final mod = modifierLabel(platform);
  final layout = ref.read(layoutNotifierProvider);
  final layoutNotifier = ref.read(layoutNotifierProvider.notifier);
  final workspace = ref.read(workspaceNotifierProvider).activeWorkspace;
  final editor = ref.read(editorNotifierProvider);
  final eventBus = ref.read(ideEventBusProvider);
  final hasWorkspace = workspace != null;
  final activeTab = editor.activeTab;

  const noWorkspace = 'No workspace is open';

  return [
    AuraCommandItem(
      id: 'file.goToFile',
      group: 'File',
      title: 'Go to File...',
      icon: Icons.insert_drive_file_outlined,
      trailing: '$mod+P',
      disabledReason: hasWorkspace ? null : noWorkspace,
      onInvoke: hasWorkspace
          ? () => eventBus.publish(const ShowQuickOpenRequested())
          : null,
    ),
    AuraCommandItem(
      id: 'file.save',
      group: 'File',
      title: 'Save File',
      icon: Icons.save_outlined,
      trailing: '$mod+S',
      disabledReason: activeTab == null
          ? 'No file is open'
          : (!activeTab.isDirty ? 'No unsaved changes' : null),
      onInvoke: activeTab != null && activeTab.isDirty
          // The same function Ctrl/Cmd+S runs -- see desktop_actions.dart.
          ? () => saveActiveFile(ref)
          : null,
    ),
    AuraCommandItem(
      id: 'file.closeTab',
      group: 'File',
      title: 'Close Editor',
      icon: Icons.close,
      trailing: '$mod+W',
      disabledReason: activeTab == null ? 'No file is open' : null,
      onInvoke: activeTab == null
          ? null
          : () => ref.read(editorNotifierProvider.notifier).closeTab(activeTab.id),
    ),
    AuraCommandItem(
      id: 'view.explorer',
      group: 'View',
      title: 'Show Explorer',
      icon: Icons.folder_outlined,
      trailing: '$mod+B',
      onInvoke: layoutNotifier.showExplorerPanel,
    ),
    AuraCommandItem(
      id: 'view.sourceControl',
      group: 'View',
      title: 'Show Source Control',
      icon: Icons.source_outlined,
      onInvoke: layoutNotifier.showSourceControlPanel,
    ),
    AuraCommandItem(
      id: 'view.controlCenter',
      group: 'View',
      title: 'Show AURA Control Center',
      icon: Icons.auto_awesome_outlined,
      onInvoke: layoutNotifier.showControlCenterPanel,
    ),
    AuraCommandItem(
      id: 'view.toggleSidebar',
      group: 'View',
      title: layout.showPrimarySidebar ? 'Hide Side Bar' : 'Show Side Bar',
      icon: Icons.vertical_split_outlined,
      trailing: '$mod+B',
      onInvoke: layoutNotifier.togglePrimarySidebar,
    ),
    AuraCommandItem(
      id: 'view.togglePanel',
      group: 'View',
      title: layout.showBottomPanel ? 'Hide Panel' : 'Show Panel',
      icon: Icons.terminal_outlined,
      trailing: '$mod+Shift+J',
      onInvoke: layoutNotifier.toggleBottomPanel,
    ),
    AuraCommandItem(
      id: 'view.focusTerminal',
      group: 'View',
      title: 'Focus Terminal',
      icon: Icons.keyboard_command_key,
      trailing: '$mod+`',
      onInvoke: () {
        if (!ref.read(layoutNotifierProvider).showBottomPanel) {
          layoutNotifier.toggleBottomPanel();
        }
        eventBus.publish(const FocusTerminalRequested());
      },
    ),
    AuraCommandItem(
      id: 'aura.toggleAgentPanel',
      group: 'AURA',
      title: layout.showSecondarySidebar ? 'Hide AURA Agent' : 'Show AURA Agent',
      icon: Icons.smart_toy_outlined,
      trailing: '$mod+J',
      onInvoke: layoutNotifier.toggleSecondarySidebar,
    ),
    AuraCommandItem(
      id: 'aura.inlineCompletion',
      group: 'AURA',
      title: 'Request Inline Completion',
      icon: Icons.auto_fix_high_outlined,
      trailing: '$mod+Space',
      disabledReason: activeTab == null ? 'No file is open' : null,
      onInvoke: activeTab == null
          ? null
          : () => eventBus.publish(const RequestInlineCompletionRequested()),
    ),
    AuraCommandItem(
      id: 'git.refresh',
      group: 'Git',
      title: 'Refresh Source Control',
      icon: Icons.refresh,
      disabledReason: hasWorkspace ? null : noWorkspace,
      onInvoke: hasWorkspace
          ? () {
              layoutNotifier.showSourceControlPanel();
              ref.read(gitNotifierProvider.notifier).refresh(workspace.id, workspace.rootPath);
            }
          : null,
    ),
  ];
}
