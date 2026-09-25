/// Explorer panel for Desktop IDE.
///
/// File tree with hostd integration and dart:io fallback.
library;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/explorer/explorer_access.dart';
import 'package:aura_client/desktop/explorer/explorer_tree_tile.dart';
import 'package:aura_client/desktop/explorer/explorer_service.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

class ExplorerPanel extends ConsumerStatefulWidget {
  const ExplorerPanel({super.key});

  @override
  ConsumerState<ExplorerPanel> createState() => _ExplorerPanelState();
}

class _ExplorerPanelState extends ConsumerState<ExplorerPanel> {
  // Lets the toolbar's Refresh button reach into the (stateful) tree below and re-fetch on
  // demand, without the tree re-fetching on every unrelated rebuild -- see _ExplorerTree's own
  // doc comment for the bug this replaced.
  final _treeKey = GlobalKey<_ExplorerTreeState>();

  @override
  Widget build(BuildContext context) {
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
                onPressed: () {
                  ref.invalidate(explorerServiceProvider);
                  _treeKey.currentState?.reload();
                },
                tooltip: 'Refresh',
              ),
            ],
          ),
        ),
        // Tree
        Expanded(
          child: _ExplorerTree(
            key: _treeKey,
            workspaceId: workspace.id,
            workspaceRoot: workspace.rootPath,
            grantedRoots: hostState.grantedRoots,
            ideDensity: ideDensity,
          ),
        ),
      ],
    );
  }
}

/// Loads the workspace's top-level directory listing once per [workspaceRoot] rather than on
/// every rebuild. The previous implementation called `service.listDirectory(...)` directly inside
/// a stateless `build()` and fed the resulting (always-new) `Future` straight to a `FutureBuilder`
/// -- since `ExplorerPanel` also watches `hostNotifierProvider` (which polls daemon status every
/// few seconds), that meant a brand-new `Future` on every poll tick, which `FutureBuilder` treats
/// as a fresh async operation: the whole tree reset to its loading spinner and re-fetched every
/// few seconds, discarding scroll position and making a click land on a widget mid-teardown. This
/// mirrors the explicit "refresh only when the key changes" guard `GitNotifier`/
/// `DiagnosticsNotifier` already use for exactly this reason.
class _ExplorerTree extends ConsumerStatefulWidget {
  const _ExplorerTree({
    super.key,
    required this.workspaceId,
    required this.workspaceRoot,
    required this.grantedRoots,
    required this.ideDensity,
  });

  final String workspaceId;
  final String workspaceRoot;
  final List<String> grantedRoots;
  final AuraIdeDensity ideDensity;

  @override
  ConsumerState<_ExplorerTree> createState() => _ExplorerTreeState();
}

class _ExplorerTreeState extends ConsumerState<_ExplorerTree> {
  DirectoryListing? _listing;
  bool _loading = true;
  String? _error;
  bool _accessDenied = false;
  String? _loadedForRoot;

  @override
  void initState() {
    super.initState();
    _maybeLoad();
  }

  @override
  void didUpdateWidget(covariant _ExplorerTree oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.workspaceRoot != widget.workspaceRoot) {
      _loadedForRoot = null;
      _maybeLoad();
    } else if (!listEquals(oldWidget.grantedRoots, widget.grantedRoots)) {
      // Granted roots arrive asynchronously (host poll, or the Grant Access
      // flow above): a newly granted root must re-fetch even though the
      // workspace root itself did not change.
      reload();
    }
  }

  void _maybeLoad() {
    if (_loadedForRoot == widget.workspaceRoot) return;
    _loadedForRoot = widget.workspaceRoot;
    reload();
  }

  Future<void> reload() async {
    if (mounted) setState(() => _loading = true);
    final service = ref.read(explorerServiceProvider);
    final hostConnected = ref.read(hostNotifierProvider).isConnected;
    final listing = await service.listDirectory(
      relativePath: '',
      grantedRoots: widget.grantedRoots,
      hostConnected: hostConnected,
    );
    if (!mounted) return;
    setState(() {
      _listing = listing;
      _error = listing.error;
      _accessDenied = listing.isAccessDenied;
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);

    if (_loading && _listing == null) {
      return Center(child: CircularProgressIndicator(color: palette.brand));
    }

    if (_error != null) {
      return _ErrorView(
        message: _error!,
        onRetry: reload,
        palette: palette,
        isAccessDenied: _accessDenied,
        workspaceRoot: widget.workspaceRoot,
        onGranted: reload,
      );
    }

    final entries = _listing?.entries ?? [];
    return ListView.builder(
      itemCount: entries.length,
      itemBuilder: (context, index) {
        final entry = entries[index];
        return ExplorerTreeTile(
          entry: entry,
          workspaceId: widget.workspaceId,
          workspaceRoot: widget.workspaceRoot,
          grantedRoots: widget.grantedRoots,
          ideDensity: widget.ideDensity,
          depth: 0,
          onChanged: reload,
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

class _ErrorView extends ConsumerWidget {
  const _ErrorView({
    required this.message,
    required this.onRetry,
    required this.palette,
    this.isAccessDenied = false,
    this.workspaceRoot,
    this.onGranted,
  });

  final String message;
  final VoidCallback onRetry;
  final AuraPalette palette;

  /// True when the failure is an access/containment refusal rather than a
  /// transient I/O error -- the remediation is granting the root, not retrying.
  final bool isAccessDenied;

  /// Absolute workspace root to offer for granting. Null hides the button.
  final String? workspaceRoot;

  /// Called after a successful grant so the caller re-fetches.
  final VoidCallback? onGranted;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AuraSpacing.lg),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.error_outline, size: 48, color: palette.danger),
            const SizedBox(height: AuraSpacing.md),
            Text(
              isAccessDenied ? 'Workspace access required' : 'Failed to load directory',
              style: TextStyle(color: palette.textPrimary, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: AuraSpacing.xs),
            Text(
              isAccessDenied
                  ? 'This folder is outside every root granted to the host daemon. '
                    'Grant access to browse it here and let the AURA agent work in it.'
                  : message,
              style: TextStyle(color: palette.textSecondary, fontSize: 12),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: AuraSpacing.lg),
            if (isAccessDenied && workspaceRoot != null)
              AuraButton(
                label: 'Grant Access',
                icon: Icons.key,
                onPressed: () => grantWorkspaceAccess(context, ref, workspaceRoot!, onGranted: onGranted),
              ),
            if (isAccessDenied && workspaceRoot != null) const SizedBox(height: AuraSpacing.sm),
            AuraButton(
              label: 'Retry',
              icon: Icons.refresh,
              variant: AuraButtonVariant.secondary,
              onPressed: onRetry,
            ),
          ],
        ),
      ),
    );
  }
}