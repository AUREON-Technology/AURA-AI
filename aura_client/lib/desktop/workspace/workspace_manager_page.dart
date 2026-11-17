/// Workspace manager page for Desktop IDE.
///
/// Full workspace management UI with recent list, open folder, remove recent.
library;

import 'package:aura_client/core/locale/relative_time.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:file_picker/file_picker.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/hostd/hostd_providers.dart' hide HostdRoot;
import 'package:aura_client/desktop/state/state_notifiers.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/l10n/l10n.dart';

class WorkspaceManagerPage extends ConsumerWidget {
  const WorkspaceManagerPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final ideDensity = context.ideDensity;
    final workspaceState = ref.watch(workspaceNotifierProvider);

    return Scaffold(
      appBar: AppBar(
        title: Text(context.l10n.workspaceManagerTitle),
        actions: [
          IconButton(
            icon: const Icon(Icons.add),
            onPressed: () => _openFolder(context, ref),
            tooltip: context.l10n.workspaceOpenFolder,
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
                    context.l10n.workspaceRecentWorkspaces,
                    style: Theme.of(context).textTheme.titleLarge?.copyWith(
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                AuraButton(
                  label: context.l10n.workspaceOpenFolder,
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
                  separatorBuilder: (_, _) => SizedBox(height: ideDensity.spacingScale * AuraSpacing.sm),
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
                                              context.l10n.workspaceActive,
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
                                      context.l10n.workspaceLastOpened(relativeDay(context.l10n, ws.lastOpenedAt)),
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
                                      label: context.l10n.workspaceOpen,
                                      variant: AuraButtonVariant.secondary,
                                      onPressed: () => ref.read(workspaceNotifierProvider.notifier).setActiveWorkspace(ws.id),
                                    ),
                                  const SizedBox(width: AuraSpacing.sm),
                                  IconButton(
                                    icon: Icon(Icons.delete_outline, size: 18, color: palette.textDisabled),
                                    onPressed: () => _confirmRemove(context, ref, ws),
                                    tooltip: context.l10n.workspaceRemoveFromRecent,
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

    final hostStatus = await ref.read(hostdControlClientProvider).status().catchError((_) => <String, dynamic>{});
    if (hostStatus['authenticated'] == true) {
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
            AuraSnackbar.show(context, message: context.l10n.explorerGrantFailed(e.toString()), kind: AuraSnackbarKind.error);
          }
        }
      }
    }
  }

  Future<bool?> _showGrantDialog(BuildContext context, String path) {
    return AuraDialog.show<bool>(
      context: context,
      title: context.l10n.explorerGrantTitle,
      child: Text(context.l10n.workspaceGrantBody(path)),
      confirmLabel: context.l10n.explorerGrant,
      cancelLabel: context.l10n.workspaceNotNow,
      onConfirm: () => Navigator.of(context).pop(true),
    );
  }

  Future<void> _confirmRemove(BuildContext context, WidgetRef ref, DesktopWorkspace ws) async {
    final confirmed = await AuraDialog.show<bool>(
      context: context,
      title: context.l10n.workspaceRemoveTitle,
      child: Text(context.l10n.workspaceRemoveBody(ws.displayName)),
      confirmLabel: context.l10n.workspaceRemove,
      cancelLabel: context.l10n.cancel,
      confirmDestructive: true,
      onConfirm: () => Navigator.of(context).pop(true),
    );
    if (confirmed == true) {
      ref.read(workspaceNotifierProvider.notifier).removeWorkspace(ws.id);
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
            context.l10n.workspaceEmptyTitle,
            style: Theme.of(context).textTheme.titleLarge?.copyWith(
              color: palette.textSecondary,
            ),
          ),
          const SizedBox(height: AuraSpacing.md),
          Text(
            context.l10n.workspaceEmptyMessage,
            style: TextStyle(color: palette.textDisabled),
          ),
          const SizedBox(height: AuraSpacing.xl),
          AuraButton(
            label: context.l10n.workspaceOpenFolder,
            icon: Icons.folder_open,
            onPressed: onOpenFolder,
          ),
        ],
      ),
    );
  }
}