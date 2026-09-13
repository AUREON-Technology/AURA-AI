/// Workspace manager page for Desktop IDE.
///
/// Full workspace management UI with recent list, open folder, remove recent.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:file_picker/file_picker.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart' hide HostdRoot;
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/events/ide_event.dart';

class WorkspaceManagerPage extends ConsumerWidget {
  const WorkspaceManagerPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final ideDensity = context.ideDensity;
    final workspaceState = ref.watch(workspaceNotifierProvider);
    final hostState = ref.watch(hostNotifierProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Workspaces'),
        actions: [
          IconButton(
            icon: const Icon(Icons.add),
            onPressed: () => _openFolder(context, ref),
            tooltip: 'Open Folder',
          ),
        ],
      ),
      body: Padding(
        padding: EdgeInsets.all(ideDensity.spacingScale * AuraSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    'Recent Workspaces',
                    style: Theme.of(context).textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                AuraButton(
                  label: 'Open Folder',
                  icon: Icons.folder_open,
                  variant: AuraButtonVariant.secondary,
                  onPressed: () => _openFolder(context, ref),
                ),
              ],
            ),
            const SizedBox(height: AuraSpacing.lg),
            if (workspaceState.workspaces.isEmpty)
              _EmptyWorkspaces(
                palette: palette,
                ideDensity: ideDensity,
                onOpenFolder: () => _openFolder(context, ref),
              )
            else
              Expanded(
                child: ListView.separated(
                  itemCount: workspaceState.workspaces.length,
                  separatorBuilder: (_, __) => SizedBox(height: ideDensity.spacingScale * AuraSpacing.sm),
                  itemBuilder: (context, index) {
                    final ws = workspaceState.workspaces[index];
                    final isActive = ws.id == workspaceState.activeWorkspaceId;
                    return AuraCard(
                      child: InkWell(
                        onTap: () => ref.read(workspaceNotifierProvider.notifier).setActiveWorkspace(ws.id),
                        borderRadius: BorderRadius.circular(AuraRadius.md),
                        child: Padding(
                          padding: EdgeInsets.all(ideDensity.spacingScale * AuraSpacing.md),
                          child: Row(
                            children: [
                              Icon(
                                Icons.folder_outlined,
                                color: isActive ? palette.brand : palette.textSecondary,
                              ),
                              const SizedBox(width: AuraSpacing.md),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Row(
                                      children: [
                                        Text(
                                          ws.displayName,
                                          style: TextStyle(
                                            fontWeight: isActive ? FontWeight.w600 : FontWeight.w400,
                                            color: palette.textPrimary,
                                          ),
                                        ),
                                        if (isActive) ...[
                                          const SizedBox(width: AuraSpacing.sm),
                                          Container(
                                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                                            decoration: BoxDecoration(
                                              color: palette.brand.withValues(alpha: 0.1),
                                              borderRadius: BorderRadius.circular(AuraRadius.pill),
                                            ),
                                            child: Text(
                                              'Active',
                                              style: TextStyle(
                                                fontSize: 10,
                                                color: palette.brand,
                                                fontWeight: FontWeight.w600,
                                              ),
                                            ),
                                          ),
                                        ],
                                      ],
                                    ),
                                    const SizedBox(height: 2),
                                    Text(
                                      ws.rootPath,
                                      style: TextStyle(
                                        fontSize: 12,
                                        color: palette.textSecondary,
                                        fontFamily: 'Courier',
                                      ),
                                      overflow: TextOverflow.ellipsis,
                                    ),
                                    const SizedBox(height: 2),
                                    Text(
                                      'Last opened: ${_formatDate(ws.lastOpenedAt)}',
                                      style: TextStyle(
                                        fontSize: 11,
                                        color: palette.textDisabled,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                              Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  if (!isActive)
                                    AuraButton(
                                      label: 'Open',
                                      variant: AuraButtonVariant.secondary,
                                      size: AuraButtonSize.small,
                                      onPressed: () => ref.read(workspaceNotifierProvider.notifier).setActiveWorkspace(ws.id),
                                    ),
                                  const SizedBox(width: AuraSpacing.sm),
                                  IconButton(
                                    icon: Icon(Icons.delete_outline, size: 18, color: palette.textDisabled),
                                    onPressed: () => _confirmRemove(context, ref, ws),
                                    tooltip: 'Remove from recent',
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                      ),
                    );
                  },
                ),
              ),
          ],
        ),
      ),
    );
  }

  Future<void> _openFolder(BuildContext context, WidgetRef ref) async {
    final path = await FilePicker.platform.getDirectoryPath();
    if (path == null) return;

    final workspaceId = 'ws_${DateTime.now().millisecondsSinceEpoch}';
    final displayName = path.split('/').last.split('\\').last;

    await ref.read(workspaceNotifierProvider.notifier).createWorkspace(
      id: workspaceId,
      rootPath: path,
      displayName: displayName,
    );

    if (ref.read(hostNotifierProvider).isConnected) {
      if (!context.mounted) return;
      final shouldGrant = await _showGrantDialog(context, path);
      if (shouldGrant == true) {
        try {
          await ref.read(hostdControlClientProvider).grantRoot(path);
          ref.read(workspaceNotifierProvider.notifier).updateHostdRoots(
            [...ref.read(workspaceNotifierProvider).hostdRoots, HostdRoot(path: path, trusted: false)],
          );
        } catch (e) {
          if (context.mounted) {
            AuraSnackbar.show(context, message: 'Could not grant root: $e', kind: AuraSnackbarKind.error);
          }
        }
      }
    }
  }

  Future<bool?> _showGrantDialog(BuildContext context, String path) {
    return AuraDialog.show<bool>(
      context: context,
      title: 'Grant workspace to hostd?',
      child: Text('This allows the AURA agent to read/write files in "$path".'),
      confirmLabel: 'Grant',
      cancelLabel: 'Not Now',
      onConfirm: () => Navigator.of(context).pop(true),
    );
  }

  Future<void> _confirmRemove(BuildContext context, WidgetRef ref, DesktopWorkspace ws) async {
    final confirmed = await AuraDialog.show<bool>(
      context: context,
      title: 'Remove workspace?',
      child: Text('This will remove "${ws.displayName}" from your recent workspaces.'),
      confirmLabel: 'Remove',
      cancelLabel: 'Cancel',
      confirmDestructive: true,
      onConfirm: () => Navigator.of(context).pop(true),
    );
    if (confirmed == true) {
      ref.read(workspaceNotifierProvider.notifier).removeWorkspace(ws.id);
    }
  }

  String _formatDate(DateTime date) {
    final now = DateTime.now();
    final diff = now.difference(date);
    if (diff.inDays == 0) {
      return 'Today ${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}';
    } else if (diff.inDays == 1) {
      return 'Yesterday';
    } else if (diff.inDays < 7) {
      return '${diff.inDays} days ago';
    } else {
      return '${date.day}/${date.month}/${date.year}';
    }
  }
}

class _EmptyWorkspaces extends StatelessWidget {
  const _EmptyWorkspaces({
    required this.palette,
    required this.ideDensity,
    required this.onOpenFolder,
  });

  final AuraPalette palette;
  final AuraIdeDensity ideDensity;
  final VoidCallback onOpenFolder;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.folder_outlined, size: 96, color: palette.textDisabled),
          const SizedBox(height: AuraSpacing.lg),
          Text(
            'No workspaces yet',
            style: Theme.of(context).textTheme.titleLarge?.copyWith(
              color: palette.textSecondary,
            ),
          ),
          const SizedBox(height: AuraSpacing.md),
          Text(
            'Open a folder to create your first workspace',
            style: TextStyle(color: palette.textDisabled),
          ),
          const SizedBox(height: AuraSpacing.xl),
          AuraButton(
            label: 'Open Folder',
            icon: Icons.folder_open,
            size: AuraButtonSize.large,
            onPressed: onOpenFolder,
          ),
        ],
      ),
    );
  }
}