/// Keyboard shortcuts for Desktop IDE.
///
/// Handles Ctrl+S, Ctrl+W, Ctrl+P, Ctrl+`, Ctrl+B, etc.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/desktop/shell/desktop_actions.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/events/ide_event.dart';

/// Widget that wraps the desktop shell with keyboard shortcuts.
class DesktopShortcuts extends ConsumerWidget {
  const DesktopShortcuts({super.key, required this.child, this.onDismissOverlay});

  final Widget child;

  /// Dismisses whatever panel is currently floating over the editor, or
  /// `null` when nothing is. Escape is bound only while this is non-null, so
  /// the key keeps its ordinary meaning (and reaches whatever else wants it)
  /// the rest of the time.
  ///
  /// This lives here rather than in the shell's own subtree because shortcut
  /// resolution walks *up* from the focused node, and the [Focus] below
  /// autofocuses the entire shell — a Shortcuts widget inside it would never
  /// be reached.
  final VoidCallback? onDismissOverlay;

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
      // Command palette. Ctrl/Cmd+Shift+P rather than Ctrl/Cmd+K, which the
      // mobile shell already uses for its own palette -- the two are separate
      // surfaces today (see desktop_commands.dart) and giving them the same
      // binding would imply otherwise.
      const SingleActivator(LogicalKeyboardKey.keyP, control: true, shift: true):
          const _CommandPaletteIntent(),
      const SingleActivator(LogicalKeyboardKey.keyP, meta: true, shift: true):
          const _CommandPaletteIntent(),
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
      if (onDismissOverlay != null)
        const SingleActivator(LogicalKeyboardKey.escape): const _DismissOverlayIntent(),
    };

    return Actions(
      actions: {
        _SaveIntent: CallbackAction<_SaveIntent>(onInvoke: (_) => saveActiveFile(ref, context: context)),
        _CloseTabIntent: CallbackAction<_CloseTabIntent>(onInvoke: (_) => _handleCloseTab(ref)),
        _QuickOpenIntent: CallbackAction<_QuickOpenIntent>(onInvoke: (_) => _handleQuickOpen(ref)),
        _CommandPaletteIntent: CallbackAction<_CommandPaletteIntent>(
          onInvoke: (_) =>
              ref.read(ideEventBusProvider).publish(const ShowCommandPaletteRequested()),
        ),
        _FocusTerminalIntent: CallbackAction<_FocusTerminalIntent>(onInvoke: (_) => _handleFocusTerminal(ref)),
        _ToggleSidebarIntent: CallbackAction<_ToggleSidebarIntent>(onInvoke: (_) => _handleToggleSidebar(ref)),
        _ToggleSecondarySidebarIntent: CallbackAction<_ToggleSecondarySidebarIntent>(onInvoke: (_) => _handleToggleSecondarySidebar(ref)),
        _ToggleBottomPanelIntent: CallbackAction<_ToggleBottomPanelIntent>(onInvoke: (_) => _handleToggleBottomPanel(ref)),
        _RequestInlineCompletionIntent: CallbackAction<_RequestInlineCompletionIntent>(onInvoke: (_) => _handleRequestInlineCompletion(ref)),
        if (onDismissOverlay != null)
          _DismissOverlayIntent: CallbackAction<_DismissOverlayIntent>(
            onInvoke: (_) => onDismissOverlay!(),
          ),
      },
      child: Shortcuts(shortcuts: shortcuts, child: Focus(autofocus: true, child: child)),
    );
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
class _CommandPaletteIntent extends Intent { const _CommandPaletteIntent(); }
class _FocusTerminalIntent extends Intent { const _FocusTerminalIntent(); }
class _ToggleSidebarIntent extends Intent { const _ToggleSidebarIntent(); }
class _ToggleSecondarySidebarIntent extends Intent { const _ToggleSecondarySidebarIntent(); }
class _ToggleBottomPanelIntent extends Intent { const _ToggleBottomPanelIntent(); }
class _RequestInlineCompletionIntent extends Intent { const _RequestInlineCompletionIntent(); }
class _DismissOverlayIntent extends Intent { const _DismissOverlayIntent(); }