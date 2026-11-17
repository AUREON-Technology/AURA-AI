/// Editor tab bar for Desktop IDE.
///
/// Shows open tabs with close buttons, dirty indicators, pin support.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/editor/editor_tab.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/desktop/workspace/workspace_file_service.dart';
import 'package:aura_client/l10n/l10n.dart';

class EditorTabBar extends ConsumerWidget {
  const EditorTabBar({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final editorState = ref.watch(editorNotifierProvider);
    final palette = AuraPalette.of(context);
    final ideDensity = context.ideDensity;

    return Container(
      height: ideDensity.tabHeight,
      color: palette.surface,
      child: Row(
        children: [
          // Tabs
          Expanded(
            child: ListView.builder(
              scrollDirection: Axis.horizontal,
              itemCount: editorState.tabs.length,
              itemBuilder: (context, index) {
                final tab = editorState.tabs[index];
                final isActive = editorState.activeTabId == tab.id;
                return _EditorTabItem(
                  tab: tab,
                  isActive: isActive,
                  ideDensity: ideDensity,
                  onTap: () => ref.read(editorNotifierProvider.notifier).setActiveTab(tab.id),
                  onClose: () => _closeTab(context, ref, tab),
                  onPin: () => ref.read(editorNotifierProvider.notifier).updateTab(
                    tab.id,
                    (t) => t.copyWith(isPinned: !t.isPinned),
                  ),
                );
              },
            ),
          ),
          // New tab button
          _NewTabButton(ideDensity: ideDensity),
        ],
      ),
    );
  }

  Future<void> _closeTab(BuildContext context, WidgetRef ref, EditorTab tab) async {
    final notifier = ref.read(editorNotifierProvider.notifier);
    if (!tab.isDirty) {
      await notifier.closeTab(tab.id);
      return;
    }

    final choice = await showDialog<_UnsavedChoice>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(dialogContext.l10n.editorUnsavedTitle),
        content: Text(dialogContext.l10n.editorUnsavedBody(tab.title)),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(),
            child: Text(dialogContext.l10n.cancel),
          ),
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(_UnsavedChoice.discard),
            child: Text(dialogContext.l10n.editorDiscard),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(_UnsavedChoice.save),
            child: Text(dialogContext.l10n.commonSave),
          ),
        ],
      ),
    );

    if (choice == null) return; // Cancel or dismissed — keep the tab open

    if (choice == _UnsavedChoice.save) {
      final workspace = ref.read(workspaceNotifierProvider).activeWorkspace;
      final content = notifier.contentFor(tab.id);
      if (workspace != null && content != null) {
        await WorkspaceFileService(workspace.rootPath).writeFile(tab.path, content);
      }
    }
    await notifier.closeTab(tab.id, force: true);
  }
}

enum _UnsavedChoice { save, discard }

class _EditorTabItem extends StatelessWidget {
  const _EditorTabItem({
    required this.tab,
    required this.isActive,
    required this.ideDensity,
    required this.onTap,
    required this.onClose,
    required this.onPin,
  });

  final EditorTab tab;
  final bool isActive;
  final AuraIdeDensity ideDensity;
  final VoidCallback onTap;
  final VoidCallback onClose;
  final VoidCallback onPin;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);

    return MouseRegion(
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        onTap: onTap,
        onSecondaryTap: onClose,
        child: Container(
          constraints: const BoxConstraints(minWidth: 120, maxWidth: 240),
          margin: EdgeInsets.symmetric(
            horizontal: ideDensity.spacingScale * AuraSpacing.xs,
            vertical: ideDensity.spacingScale * AuraSpacing.xs,
          ),
          padding: EdgeInsets.symmetric(
            horizontal: ideDensity.spacingScale * AuraSpacing.md,
            vertical: ideDensity.spacingScale * AuraSpacing.xs,
          ),
          decoration: BoxDecoration(
            color: isActive ? palette.surfaceVariant : palette.surface,
            borderRadius: BorderRadius.vertical(
              top: Radius.circular(AuraRadius.sm),
            ),
            border: Border.all(
              color: isActive ? palette.brand : palette.border,
              width: isActive ? 1.5 : 1,
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              // File icon
              Icon(
                _getFileIcon(tab.path),
                size: 14,
                color: isActive ? palette.textPrimary : palette.textSecondary,
              ),
              const SizedBox(width: AuraSpacing.xs),
              // Title
              Flexible(
                child: Text(
                  tab.title,
                  style: TextStyle(
                    fontSize: 12,
                    color: isActive ? palette.textPrimary : palette.textSecondary,
                    fontWeight: isActive ? FontWeight.w500 : FontWeight.w400,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              // Dirty indicator
              if (tab.isDirty)
                Padding(
                  padding: const EdgeInsetsDirectional.only(start: 4),
                  child: Container(
                    width: 6,
                    height: 6,
                    decoration: BoxDecoration(
                      color: palette.warning,
                      shape: BoxShape.circle,
                    ),
                  ),
                ),
              // Pinned indicator
              if (tab.isPinned)
                Padding(
                  padding: const EdgeInsetsDirectional.only(start: 4),
                  child: Icon(
                    Icons.push_pin,
                    size: 12,
                    color: palette.brand,
                  ),
                ),
              const SizedBox(width: AuraSpacing.xs),
              // Close button
              InkWell(
                onTap: onClose,
                borderRadius: BorderRadius.circular(AuraRadius.sm),
                child: Padding(
                  padding: const EdgeInsets.all(2),
                  child: Icon(
                    Icons.close,
                    size: 14,
                    color: isActive ? palette.textPrimary : palette.textSecondary,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  IconData _getFileIcon(String path) {
    final ext = path.split('.').last.toLowerCase();
    switch (ext) {
      case 'dart': return Icons.code;
      case 'py': return Icons.code;
      case 'js': return Icons.code;
      case 'ts': return Icons.code;
      case 'json': return Icons.data_object;
      case 'yaml':
      case 'yml': return Icons.settings;
      case 'md': return Icons.description;
      case 'txt': return Icons.text_snippet;
      case 'sh': return Icons.terminal;
      case 'ps1': return Icons.terminal;
      default: return Icons.insert_drive_file;
    }
  }
}

class _NewTabButton extends ConsumerWidget {
  const _NewTabButton({required this.ideDensity});

  final AuraIdeDensity ideDensity;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    return Padding(
      padding: EdgeInsets.all(ideDensity.spacingScale * AuraSpacing.xs),
      child: InkWell(
        onTap: () => _showQuickOpen(ref),
        borderRadius: BorderRadius.circular(AuraRadius.sm),
        child: Container(
          width: 28,
          height: 28,
          decoration: BoxDecoration(
            color: palette.surfaceVariant,
            borderRadius: BorderRadius.circular(AuraRadius.sm),
            border: Border.all(color: palette.border),
          ),
          child: Icon(Icons.add, size: 16, color: palette.textSecondary),
        ),
      ),
    );
  }

  void _showQuickOpen(WidgetRef ref) {
    ref.read(ideEventBusProvider).publish(const ShowQuickOpenRequested());
  }
}