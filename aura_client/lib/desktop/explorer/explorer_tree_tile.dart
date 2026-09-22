/// Explorer tree tile for Desktop IDE.
///
/// Recursive tree widget with expand/collapse, icons by extension.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/explorer/explorer_service.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

class ExplorerTreeTile extends ConsumerWidget {
  const ExplorerTreeTile({
    super.key,
    required this.entry,
    required this.workspaceId,
    required this.workspaceRoot,
    required this.grantedRoots,
    required this.ideDensity,
    required this.depth,
  });

  final FileSystemEntry entry;
  final String workspaceId;
  final String workspaceRoot;
  final List<String> grantedRoots;
  final AuraIdeDensity ideDensity;
  final int depth;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);

    if (!entry.isDirectory) {
      return _FileTile(
        entry: entry,
        workspaceId: workspaceId,
        depth: depth,
        ideDensity: ideDensity,
        palette: palette,
      );
    }

    return _DirectoryTile(
      entry: entry,
      workspaceId: workspaceId,
      workspaceRoot: workspaceRoot,
      grantedRoots: grantedRoots,
      ideDensity: ideDensity,
      depth: depth,
      palette: palette,
    );
  }
}

class _FileTile extends ConsumerWidget {
  const _FileTile({
    required this.entry,
    required this.workspaceId,
    required this.depth,
    required this.ideDensity,
    required this.palette,
  });

  final FileSystemEntry entry;
  final String workspaceId;
  final int depth;
  final AuraIdeDensity ideDensity;
  final AuraPalette palette;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Padding(
      padding: EdgeInsets.only(left: depth * ideDensity.treeRowHeight),
      child: SizedBox(
        height: ideDensity.treeRowHeight,
        child: InkWell(
          onTap: () => _openFile(ref),
          borderRadius: BorderRadius.circular(AuraRadius.sm),
          child: Row(
            children: [
              Icon(
                _getFileIcon(entry.extension),
                size: 14,
                color: palette.textSecondary,
              ),
              const SizedBox(width: AuraSpacing.xs),
              Expanded(
                child: Text(
                  entry.name,
                  style: TextStyle(
                    fontSize: 12,
                    color: palette.textPrimary,
                    fontFamily: entry.extension == 'dart' || entry.extension == 'py' ? 'Courier' : null,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// Mirrors `desktop_shell.dart`'s `_showQuickOpen` open-file call exactly: `entry.path` is
  /// already workspace-relative (see `FileSystemEntry.fromIO`/`.fromHostd`), so it doubles as
  /// both the tab id and the path.
  void _openFile(WidgetRef ref) {
    ref.read(editorNotifierProvider.notifier).openTab(
          tabId: entry.path,
          path: entry.path,
          workspaceId: workspaceId,
        );
  }

  IconData _getFileIcon(String? ext) {
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

class _DirectoryTile extends ConsumerStatefulWidget {
  const _DirectoryTile({
    required this.entry,
    required this.workspaceId,
    required this.workspaceRoot,
    required this.grantedRoots,
    required this.ideDensity,
    required this.depth,
    required this.palette,
  });

  final FileSystemEntry entry;
  final String workspaceId;
  final String workspaceRoot;
  final List<String> grantedRoots;
  final AuraIdeDensity ideDensity;
  final int depth;
  final AuraPalette palette;

  @override
  ConsumerState<_DirectoryTile> createState() => _DirectoryTileState();
}

class _DirectoryTileState extends ConsumerState<_DirectoryTile> {
  bool _expanded = false;
  DirectoryListing? _children;
  bool _loading = false;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final ideDensity = widget.ideDensity;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Directory header
        Padding(
          padding: EdgeInsets.only(left: widget.depth * ideDensity.treeRowHeight),
          child: SizedBox(
            height: ideDensity.treeRowHeight,
            child: InkWell(
              onTap: _toggleExpand,
              onSecondaryTap: _showContextMenu,
              borderRadius: BorderRadius.circular(AuraRadius.sm),
              child: Row(
                children: [
                  Icon(
                    _expanded ? Icons.expand_more : Icons.chevron_right,
                    size: 16,
                    color: palette.textSecondary,
                  ),
                  Icon(
                    Icons.folder_outlined,
                    size: 14,
                    color: palette.brand,
                  ),
                  const SizedBox(width: AuraSpacing.xs),
                  Expanded(
                    child: Text(
                      widget.entry.name,
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w500,
                        color: palette.textPrimary,
                      ),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
        // Children
        if (_expanded) _buildChildren(),
      ],
    );
  }

  void _toggleExpand() {
    setState(() {
      _expanded = !_expanded;
      if (_expanded && _children == null) {
        _loadChildren();
      }
    });
  }

  Future<void> _loadChildren() async {
    setState(() => _loading = true);
    try {
      final service = ref.read(explorerServiceProvider);
      final listing = await service.listDirectory(
        relativePath: widget.entry.path,
        grantedRoots: widget.grantedRoots,
      );
      if (mounted) {
        setState(() {
          _children = listing;
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() => _loading = false);
      }
    }
  }

  void _showContextMenu() {
    // TODO: Show context menu (reveal in OS, copy path, etc.)
  }

  Widget _buildChildren() {
    final ideDensity = widget.ideDensity;
    final palette = widget.palette;

    if (_loading) {
      return Padding(
        padding: EdgeInsets.only(left: (widget.depth + 1) * ideDensity.treeRowHeight),
        child: SizedBox(
          height: ideDensity.treeRowHeight,
          child: Center(
            child: SizedBox(
              width: 16,
              height: 16,
              child: CircularProgressIndicator(strokeWidth: 1.5, color: palette.brand),
            ),
          ),
        ),
      );
    }

    final entries = _children?.entries ?? [];
    if (entries.isEmpty) {
      return Padding(
        padding: EdgeInsets.only(left: (widget.depth + 1) * ideDensity.treeRowHeight),
        child: SizedBox(
          height: ideDensity.treeRowHeight,
          child: Text(
            'Empty',
            style: TextStyle(fontSize: 11, color: palette.textDisabled),
          ),
        ),
      );
    }

    return Column(
      children: entries.map((entry) {
        return ExplorerTreeTile(
          entry: entry,
          workspaceId: widget.workspaceId,
          workspaceRoot: widget.workspaceRoot,
          grantedRoots: widget.grantedRoots,
          ideDensity: ideDensity,
          depth: widget.depth + 1,
        );
      }).toList(),
    );
  }
}