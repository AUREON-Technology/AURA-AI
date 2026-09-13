/// Workspace welcome page for Desktop IDE.
///
/// Shows recent workspaces and "Open Folder" button.
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

class WorkspaceWelcomePage extends ConsumerWidget {
  const WorkspaceWelcomePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final ideDensity = context.ideDensity;
    final workspaceState = ref.watch(workspaceNotifierProvider);
    final hostState = ref.watch(hostNotifierProvider);

    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 600),
          child: Padding(
            padding: EdgeInsets.all(ideDensity.spacingScale * AuraSpacing.xl),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // Logo
                ClipRRect(
                  borderRadius: BorderRadius.circular(AuraRadius.lg),
                  child: Image.asset(
                    'assets/app_icon.png',
                    width: 96,
                    height: 96,
                    fit: BoxFit.contain,
                  ),
                ),
                const SizedBox(height: AuraSpacing.md),
                Text(
                  'AURA Desktop IDE',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                    fontWeight: FontWeight.w700,
                    color: palette.textPrimary,
                  ),
                ),
                const SizedBox(height: AuraSpacing.xs),
                Text(
                  'Open a folder to start coding with AURA',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: palette.textSecondary),
                ),
                const SizedBox(height: AuraSpacing.xxxl),

                // Open Folder button
                AuraButton(
                  label: 'Open Folder',
                  icon: Icons.folder_open,
                  size: AuraButtonSize.large,
                  onPressed: () => _openFolder(context, ref),
                ),
                const SizedBox(height: AuraSpacing.lg),

                // Divider
                Row(
                  children: [
                    Expanded(child: Divider(color: palette.border)),
                    Padding(
                      padding: EdgeInsets.symmetric(horizontal: ideDensity.spacingScale * AuraSpacing.md),
                      child: Text('Recent', style: TextStyle(color: palette.textSecondary)),
                    ),
                    Expanded(child: Divider(color: palette.border)),
                  ],
                ),
                const SizedBox(height: AuraSpacing.lg),

                // Recent workspaces
                if (workspaceState.workspaces.isEmpty)
                  _EmptyRecent(palette: palette, ideDensity: ideDensity)
                else
                  _RecentWorkspacesList(
                    workspaces: workspaceState.workspaces,
                    activeWorkspaceId: workspaceState.activeWorkspaceId,
                    ideDensity: ideDensity,
                    palette: palette,
                  ),
              ],
            ),
          ),
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

    // Offer to grant to hostd
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
}

class _EmptyRecent extends StatelessWidget {
  const _EmptyRecent({required this.palette, required this.ideDensity});

  final AuraPalette palette;
  final AuraIdeDensity ideDensity;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Icon(Icons.folder_outlined, size: 64, color: palette.textDisabled),
        const SizedBox(height: AuraSpacing.md),
        Text(
          'No recent workspaces',
          style: TextStyle(color: palette.textSecondary),
        ),
        const SizedBox(height: AuraSpacing.xs),
        Text(
          'Open a folder to get started',
          style: TextStyle(color: palette.textDisabled, fontSize: 12),
        ),
      ],
    );
  }
}

class _RecentWorkspacesList extends ConsumerWidget {
  const _RecentWorkspacesList({
    required this.workspaces,
    required this.activeWorkspaceId,
    required this.ideDensity,
    required this.palette,
  });

  final List<DesktopWorkspace> workspaces;
  final String? activeWorkspaceId;
  final AuraIdeDensity ideDensity;
  final AuraPalette palette;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Column(
      children: workspaces.map((ws) {
        final isActive = ws.id == activeWorkspaceId;
        return AuraCard(
          margin: EdgeInsets.only(bottom: ideDensity.spacingScale * AuraSpacing.sm),
          padding: EdgeInsets.all(ideDensity.spacingScale * AuraSpacing.md),
          child: InkWell(
            onTap: () => ref.read(workspaceNotifierProvider.notifier).setActiveWorkspace(ws.id),
            borderRadius: BorderRadius.circular(AuraRadius.md),
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
                      Text(
                        ws.displayName,
                        style: TextStyle(
                          fontWeight: isActive ? FontWeight.w600 : FontWeight.w400,
                          color: palette.textPrimary,
                        ),
                      ),
                      Text(
                        ws.rootPath,
                        style: TextStyle(
                          fontSize: 12,
                          color: palette.textSecondary,
                          fontFamily: 'Courier',
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ],
                  ),
                ),
                if (isActive)
                  Icon(Icons.check_circle, color: palette.brand, size: 20)
                else
                  IconButton(
                    icon: Icon(Icons.close, size: 18, color: palette.textDisabled),
                    onPressed: () => ref.read(workspaceNotifierProvider.notifier).removeWorkspace(ws.id),
                    tooltip: 'Remove from recent',
                  ),
              ],
            ),
          ),
        );
      }).toList(),
    );
  }
}