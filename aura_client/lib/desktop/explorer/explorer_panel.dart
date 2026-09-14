/// Explorer panel for Desktop IDE.
///
/// File tree with hostd integration and dart:io fallback.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/explorer/explorer_tree_tile.dart';
import 'package:aura_client/desktop/explorer/explorer_service.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

class ExplorerPanel extends ConsumerWidget {
  const ExplorerPanel({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final ideDensity = context.ideDensity;
    final workspace = ref.watch(workspaceNotifierProvider).activeWorkspace;
    final hostState = ref.watch(hostNotifierProvider);

    if (workspace == null) {
      return _NoWorkspaceSelected(palette: palette, ideDensity: ideDensity);
    }

    return Column(
      children: [
        // Toolbar
        Container(
          height: ideDensity.compactControlHeight,
          padding: EdgeInsets.symmetric(horizontal: ideDensity.spacingScale * AuraSpacing.sm),
          decoration: BoxDecoration(
            color: palette.surface,
            border: Border(bottom: BorderSide(color: palette.border)),
          ),
          child: Row(
            children: [
              Text(
                workspace.displayName,
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: palette.textPrimary,
                ),
              ),
              const Spacer(),
              if (!hostState.isConnected)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(
                    color: palette.warning.withValues(alpha: 0.1),
                    borderRadius: BorderRadius.circular(AuraRadius.pill),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.cloud_off, size: 12, color: palette.warning),
                      const SizedBox(width: 4),
                      Text(
                        'Host offline',
                        style: TextStyle(fontSize: 10, color: palette.warning),
                      ),
                    ],
                  ),
                ),
              IconButton(
                icon: Icon(Icons.refresh, size: 16, color: palette.textSecondary),
                onPressed: () => ref.invalidate(explorerServiceProvider),
                tooltip: 'Refresh',
              ),
            ],
          ),
        ),
        // Tree
        Expanded(
          child: _ExplorerTree(
            workspaceRoot: workspace.rootPath,
            grantedRoots: hostState.grantedRoots,
            ideDensity: ideDensity,
          ),
        ),
      ],
    );
  }
}

class _ExplorerTree extends ConsumerWidget {
  const _ExplorerTree({
    required this.workspaceRoot,
    required this.grantedRoots,
    required this.ideDensity,
  });

  final String workspaceRoot;
  final List<String> grantedRoots;
  final AuraIdeDensity ideDensity;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final service = ref.watch(explorerServiceProvider);

    return FutureBuilder<DirectoryListing>(
      future: service.listDirectory(
        relativePath: '',
        grantedRoots: grantedRoots,
      ),
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting) {
          return Center(
            child: CircularProgressIndicator(color: palette.brand),
          );
        }

        final listing = snapshot.data;
        if (listing?.error != null) {
          return _ErrorView(
            message: listing!.error!,
            onRetry: () => ref.invalidate(explorerServiceProvider),
            palette: palette,
          );
        }

        final entries = listing?.entries ?? [];
        return ListView.builder(
          itemCount: entries.length,
          itemBuilder: (context, index) {
            final entry = entries[index];
            return ExplorerTreeTile(
              entry: entry,
              workspaceRoot: workspaceRoot,
              grantedRoots: grantedRoots,
              ideDensity: ideDensity,
              depth: 0,
            );
          },
        );
      },
    );
  }
}

class _NoWorkspaceSelected extends StatelessWidget {
  const _NoWorkspaceSelected({required this.palette, required this.ideDensity});

  final AuraPalette palette;
  final AuraIdeDensity ideDensity;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.folder_outlined, size: 64, color: palette.textDisabled),
          const SizedBox(height: AuraSpacing.md),
          Text(
            'No workspace open',
            style: TextStyle(color: palette.textSecondary),
          ),
          const SizedBox(height: AuraSpacing.xs),
          Text(
            'Open a folder from the welcome page',
            style: TextStyle(color: palette.textDisabled, fontSize: 12),
          ),
        ],
      ),
    );
  }
}

class _ErrorView extends StatelessWidget {
  const _ErrorView({
    required this.message,
    required this.onRetry,
    required this.palette,
  });

  final String message;
  final VoidCallback onRetry;
  final AuraPalette palette;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AuraSpacing.lg),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.error_outline, size: 48, color: palette.danger),
            const SizedBox(height: AuraSpacing.md),
            Text(
              'Failed to load directory',
              style: TextStyle(color: palette.textPrimary, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: AuraSpacing.xs),
            Text(
              message,
              style: TextStyle(color: palette.textSecondary, fontSize: 12),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: AuraSpacing.lg),
            AuraButton(
              label: 'Retry',
              icon: Icons.refresh,
              onPressed: onRetry,
            ),
          ],
        ),
      ),
    );
  }
}