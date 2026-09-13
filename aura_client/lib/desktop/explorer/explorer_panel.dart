/// Explorer panel for Desktop IDE.
///
/// Shows the active workspace's file tree in the primary sidebar. Opens
/// files into the editor on tap.
library;

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path/path.dart' as p;

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/explorer/explorer_service.dart';
import 'package:aura_client/desktop/explorer/explorer_tree_tile.dart';
import 'package:aura_client/desktop/host/host_panel.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

class ExplorerPanel extends ConsumerStatefulWidget {
  const ExplorerPanel({super.key});

  @override
  ConsumerState<ExplorerPanel> createState() => _ExplorerPanelState();
}

class _ExplorerPanelState extends ConsumerState<ExplorerPanel> {
  ExplorerService? _service;
  String? _root;

  /// Cache of directory listings, keyed by relative path (`''` for root).
  final Map<String, List<ExplorerEntry>> _cache = {};
  final Set<String> _expanded = {};
  bool _loading = true;

  /// Detects an active-workspace change and (re)starts loading its tree.
  ///
  /// Called from [build] rather than [didChangeDependencies]: Riverpod
  /// rebuilds a [ConsumerState] by forcing a new [build] when a watched
  /// provider changes, not by re-running dependency-change lifecycle hooks,
  /// so that's the only place guaranteed to see the new active workspace.
  void _syncToActiveWorkspace(String? root) {
    if (root == _root) return;
    _root = root;
    _cache.clear();
    _expanded.clear();
    _service = root == null ? null : ExplorerService(root);
    if (_service != null) {
      _loading = true;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _loadRoot();
      });
    } else {
      _loading = false;
    }
  }

  Future<void> _loadRoot() async {
    setState(() => _loading = true);
    await _loadDirectory('');
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _loadDirectory(String relativePath) async {
    final service = _service;
    if (service == null) return;
    try {
      final entries = await service.list(relativePath);
      if (mounted) setState(() => _cache[relativePath] = entries);
    } catch (_) {
      if (mounted) setState(() => _cache[relativePath] = const []);
    }
  }

  Future<void> _toggle(ExplorerEntry entry) async {
    if (!entry.isDirectory) {
      _openFile(entry);
      return;
    }
    if (_expanded.contains(entry.relativePath)) {
      setState(() => _expanded.remove(entry.relativePath));
      return;
    }
    setState(() => _expanded.add(entry.relativePath));
    if (!_cache.containsKey(entry.relativePath)) {
      await _loadDirectory(entry.relativePath);
    }
  }

  void _openFile(ExplorerEntry entry) {
    final workspaceId = ref.read(workspaceNotifierProvider).activeWorkspaceId ?? '';
    ref.read(editorNotifierProvider.notifier).openTab(
          tabId: entry.relativePath,
          path: entry.relativePath,
          workspaceId: workspaceId,
        );
  }

  void _revealInOs(String relativePath) {
    final root = _root;
    if (root == null) return;
    final absolute = relativePath.isEmpty ? root : p.join(root, relativePath);
    if (Platform.isWindows) {
      Process.run('explorer.exe', [absolute]);
    } else if (Platform.isMacOS) {
      Process.run('open', [absolute]);
    } else if (Platform.isLinux) {
      Process.run('xdg-open', [absolute]);
    }
  }

  List<Widget> _buildTree(String relativePath, int depth) {
    final entries = _cache[relativePath];
    if (entries == null) return const [];
    final activeTabId = ref.watch(editorNotifierProvider).activeTabId;
    final widgets = <Widget>[];
    for (final entry in entries) {
      final isExpanded = _expanded.contains(entry.relativePath);
      widgets.add(
        ExplorerTreeTile(
          key: ValueKey(entry.relativePath),
          entry: entry,
          depth: depth,
          isExpanded: isExpanded,
          isActive: !entry.isDirectory && entry.relativePath == activeTabId,
          onTap: () => _toggle(entry),
        ),
      );
      if (entry.isDirectory && isExpanded) {
        widgets.addAll(_buildTree(entry.relativePath, depth + 1));
      }
    }
    return widgets;
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final workspace = ref.watch(workspaceNotifierProvider).activeWorkspace;
    _syncToActiveWorkspace(workspace?.rootPath);

    if (workspace == null) {
      return Container(
        color: palette.surface,
        child: Center(
          child: Text(
            'No workspace open',
            style: TextStyle(color: palette.textDisabled, fontSize: 12),
          ),
        ),
      );
    }

    if (_loading) {
      return Container(
        color: palette.surface,
        child: const Center(child: CircularProgressIndicator()),
      );
    }

    return Container(
      color: palette.surface,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.md, vertical: AuraSpacing.sm),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    workspace.displayName.toUpperCase(),
                    style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 0.5,
                      color: palette.textSecondary,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                IconButton(
                  icon: Icon(Icons.refresh, size: 16, color: palette.textSecondary),
                  tooltip: 'Refresh',
                  onPressed: () {
                    _cache.clear();
                    _loadRoot();
                  },
                  constraints: const BoxConstraints(),
                  padding: const EdgeInsets.all(4),
                ),
                IconButton(
                  icon: Icon(Icons.open_in_new, size: 15, color: palette.textSecondary),
                  tooltip: 'Reveal in Explorer',
                  onPressed: () => _revealInOs(''),
                  constraints: const BoxConstraints(),
                  padding: const EdgeInsets.all(4),
                ),
              ],
            ),
          ),
          Expanded(
            child: ListView(
              children: _buildTree('', 0),
            ),
          ),
          const HostPanel(),
        ],
      ),
    );
  }
}
