/// Keyboard shortcuts for Desktop IDE.
///
/// Handles Ctrl+S, Ctrl+W, Ctrl+P, Ctrl+`, Ctrl+B, etc.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/workspace/workspace_file_service.dart';

/// Widget that wraps the desktop shell with keyboard shortcuts.
class DesktopShortcuts extends ConsumerWidget {
  const DesktopShortcuts({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final shortcuts = <ShortcutActivator, Intent>{
      // Save
      const SingleActivator(LogicalKeyboardKey.keyS, control: true): const _SaveIntent(),
      const SingleActivator(LogicalKeyboardKey.keyS, meta: true): const _SaveIntent(),
      // Close tab
      const SingleActivator(LogicalKeyboardKey.keyW, control: true): const _CloseTabIntent(),
      const SingleActivator(LogicalKeyboardKey.keyW, meta: true): const _CloseTabIntent(),
      // Quick open
      const SingleActivator(LogicalKeyboardKey.keyP, control: true): const _QuickOpenIntent(),
      const SingleActivator(LogicalKeyboardKey.keyP, meta: true): const _QuickOpenIntent(),
      // Focus terminal
      const SingleActivator(LogicalKeyboardKey.backquote, control: true): const _FocusTerminalIntent(),
      const SingleActivator(LogicalKeyboardKey.backquote, meta: true): const _FocusTerminalIntent(),
      // Toggle sidebar
      const SingleActivator(LogicalKeyboardKey.keyB, control: true): const _ToggleSidebarIntent(),
      const SingleActivator(LogicalKeyboardKey.keyB, meta: true): const _ToggleSidebarIntent(),
      // Toggle secondary sidebar
      const SingleActivator(LogicalKeyboardKey.keyJ, control: true): const _ToggleSecondarySidebarIntent(),
      const SingleActivator(LogicalKeyboardKey.keyJ, meta: true): const _ToggleSecondarySidebarIntent(),
      // Toggle bottom panel
      const SingleActivator(LogicalKeyboardKey.keyJ, control: true, shift: true): const _ToggleBottomPanelIntent(),
      const SingleActivator(LogicalKeyboardKey.keyJ, meta: true, shift: true): const _ToggleBottomPanelIntent(),
      // D6 Part D: request an inline completion at the cursor (explicit-trigger, not
      // continuous -- see the D6 plan's Part D decision 3). Ctrl/Cmd+Space matches the
      // "trigger suggestion" binding most editors already use.
      const SingleActivator(LogicalKeyboardKey.space, control: true): const _RequestInlineCompletionIntent(),
      const SingleActivator(LogicalKeyboardKey.space, meta: true): const _RequestInlineCompletionIntent(),
    };

    return Actions(
      actions: {
        _SaveIntent: CallbackAction<_SaveIntent>(onInvoke: (_) => _handleSave(ref)),
        _CloseTabIntent: CallbackAction<_CloseTabIntent>(onInvoke: (_) => _handleCloseTab(ref)),
        _QuickOpenIntent: CallbackAction<_QuickOpenIntent>(onInvoke: (_) => _handleQuickOpen(ref)),
        _FocusTerminalIntent: CallbackAction<_FocusTerminalIntent>(onInvoke: (_) => _handleFocusTerminal(ref)),
        _ToggleSidebarIntent: CallbackAction<_ToggleSidebarIntent>(onInvoke: (_) => _handleToggleSidebar(ref)),
        _ToggleSecondarySidebarIntent: CallbackAction<_ToggleSecondarySidebarIntent>(onInvoke: (_) => _handleToggleSecondarySidebar(ref)),
        _ToggleBottomPanelIntent: CallbackAction<_ToggleBottomPanelIntent>(onInvoke: (_) => _handleToggleBottomPanel(ref)),
        _RequestInlineCompletionIntent: CallbackAction<_RequestInlineCompletionIntent>(onInvoke: (_) => _handleRequestInlineCompletion(ref)),
      },
      child: Shortcuts(shortcuts: shortcuts, child: Focus(autofocus: true, child: child)),
    );
  }

  void _handleSave(WidgetRef ref) {
    final activeTab = ref.read(editorNotifierProvider).activeTab;
    if (activeTab == null || !activeTab.isDirty) return;

    final workspace = ref.read(workspaceNotifierProvider).activeWorkspace;
    if (workspace == null) return;

    final content = ref.read(editorNotifierProvider.notifier).contentFor(activeTab.id);
    if (content == null) return;

    WorkspaceFileService(workspace.rootPath).writeFile(activeTab.path, content).then((_) {
      ref.read(editorNotifierProvider.notifier).markDirty(activeTab.id, false);
      ref.read(ideEventBusProvider).publish(FileSaved(activeTab.path, workspace.id));
    });
  }

  void _handleCloseTab(WidgetRef ref) {
    final activeTab = ref.read(editorNotifierProvider).activeTab;
    if (activeTab == null) return;
    // Silently no-ops if the tab is dirty (closeTab's default, safe behavior) —
    // the unsaved-changes prompt lives on the tab bar's close button
    // (EditorTabBar), which has a BuildContext to show it from.
    ref.read(editorNotifierProvider.notifier).closeTab(activeTab.id);
  }

  void _handleQuickOpen(WidgetRef ref) {
    ref.read(ideEventBusProvider).publish(const ShowQuickOpenRequested());
  }

  void _handleFocusTerminal(WidgetRef ref) {
    ref.read(layoutNotifierProvider.notifier).toggleBottomPanel();
    ref.read(ideEventBusProvider).publish(const FocusTerminalRequested());
  }

  void _handleToggleSidebar(WidgetRef ref) {
    ref.read(layoutNotifierProvider.notifier).togglePrimarySidebar();
  }

  void _handleToggleSecondarySidebar(WidgetRef ref) {
    ref.read(layoutNotifierProvider.notifier).toggleSecondarySidebar();
  }

  void _handleToggleBottomPanel(WidgetRef ref) {
    ref.read(layoutNotifierProvider.notifier).toggleBottomPanel();
  }

  void _handleRequestInlineCompletion(WidgetRef ref) {
    ref.read(ideEventBusProvider).publish(const RequestInlineCompletionRequested());
  }
}

// Intent classes
class _SaveIntent extends Intent { const _SaveIntent(); }
class _CloseTabIntent extends Intent { const _CloseTabIntent(); }
class _QuickOpenIntent extends Intent { const _QuickOpenIntent(); }
class _FocusTerminalIntent extends Intent { const _FocusTerminalIntent(); }
class _ToggleSidebarIntent extends Intent { const _ToggleSidebarIntent(); }
class _ToggleSecondarySidebarIntent extends Intent { const _ToggleSecondarySidebarIntent(); }
class _ToggleBottomPanelIntent extends Intent { const _ToggleBottomPanelIntent(); }
class _RequestInlineCompletionIntent extends Intent { const _RequestInlineCompletionIntent(); }