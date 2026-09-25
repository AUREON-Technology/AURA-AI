/// Explorer tree tile for Desktop IDE.
///
/// Recursive tree widget with expand/collapse, icons by extension.
library;

import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/explorer/explorer_access.dart';
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
        workspaceRoot: workspaceRoot,
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
    required this.workspaceRoot,
    required this.depth,
    required this.ideDensity,
    required this.palette,
  });

  final FileSystemEntry entry;
  final String workspaceId;
  final String workspaceRoot;
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
          onSecondaryTapDown: (details) {
            unawaited(_showContextMenu(context, ref, details.globalPosition));
          },
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

  Future<void> _showContextMenu(BuildContext context, WidgetRef ref, Offset position) async {
    final selected = await showMenu<String>(
      context: context,
      position: RelativeRect.fromLTRB(position.dx, position.dy, position.dx, position.dy),
      items: [
        _menuItem('open', Icons.open_in_new, 'Open'),
        _menuItem('copy-path', Icons.content_copy, 'Copy path'),
        _menuItem('copy-relative-path', Icons.content_copy, 'Copy relative path'),
        _menuItem('reveal', Icons.folder_open, 'Reveal in filesystem'),
      ],
    );
    if (!context.mounted) return;
    switch (selected) {
      case 'open':
        _openFile(ref);
      case 'copy-path':
        await _copyToClipboard(context, _absolutePathOf(workspaceRoot, entry.path));
      case 'copy-relative-path':
        await _copyToClipboard(context, _relativePathOf(workspaceRoot, entry.path));
      case 'reveal':
        await _revealInFilesystem(context, _absolutePathOf(workspaceRoot, entry.path), isDirectory: false);
    }
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
  Offset? _menuPosition;

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
              onSecondaryTapDown: (details) => _menuPosition = details.globalPosition,
              onSecondaryTap: () {
                unawaited(_showContextMenu(context));
              },
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
      final hostConnected = ref.read(hostNotifierProvider).isConnected;
      final listing = await service.listDirectory(
        relativePath: widget.entry.path,
        grantedRoots: widget.grantedRoots,
        hostConnected: hostConnected,
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

  Future<void> _showContextMenu(BuildContext context) async {
    final position = _menuPosition ?? Offset.zero;
    final selected = await showMenu<String>(
      context: context,
      position: RelativeRect.fromLTRB(position.dx, position.dy, position.dx, position.dy),
      items: [
        _menuItem(_expanded ? 'collapse' : 'expand', _expanded ? Icons.expand_less : Icons.expand_more,
            _expanded ? 'Collapse' : 'Expand'),
        _menuItem('refresh', Icons.refresh, 'Refresh'),
        _menuItem('copy-path', Icons.content_copy, 'Copy path'),
        _menuItem('copy-relative-path', Icons.content_copy, 'Copy relative path'),
        _menuItem('reveal', Icons.folder_open, 'Reveal in filesystem'),
      ],
    );
    if (!context.mounted) return;
    switch (selected) {
      case 'expand':
        if (!_expanded) _toggleExpand();
      case 'collapse':
        if (_expanded) _toggleExpand();
      case 'refresh':
        _refresh();
      case 'copy-path':
        await _copyToClipboard(context, _absolutePathOf(widget.workspaceRoot, widget.entry.path));
      case 'copy-relative-path':
        await _copyToClipboard(context, _relativePathOf(widget.workspaceRoot, widget.entry.path));
      case 'reveal':
        await _revealInFilesystem(context, _absolutePathOf(widget.workspaceRoot, widget.entry.path),
            isDirectory: true);
    }
  }

  /// Re-fetches this directory's children even when already expanded -- the
  /// toolbar Refresh only reaches the tree root, this covers subtrees.
  void _refresh() {
    setState(() {
      _expanded = true;
      _children = null;
      _loadChildren();
    });
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

    final children = _children;
    final entries = children?.entries ?? [];

    // A listing error (most commonly `isAccessDenied` -- see `ExplorerService.listDirectory`'s
    // doc comment) also comes back with an empty `entries` list, so it must be checked before
    // treating that as "genuinely empty" -- otherwise a folder hostd has no grant for renders as
    // a plain empty directory instead of surfacing the same Grant Access remediation the
    // top-level Explorer error view offers.
    if (children?.error != null) {
      return Padding(
        padding: EdgeInsets.only(left: (widget.depth + 1) * ideDensity.treeRowHeight),
        child: Row(
          children: [
            Icon(
              children!.isAccessDenied ? Icons.lock_outline : Icons.error_outline,
              size: 13,
              color: palette.textDisabled,
            ),
            const SizedBox(width: AuraSpacing.xs),
            Expanded(
              child: Text(
                children.isAccessDenied ? 'Access required' : 'Failed to load',
                style: TextStyle(fontSize: 11, color: palette.textDisabled),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            if (children.isAccessDenied)
              InkWell(
                onTap: () => grantWorkspaceAccess(context, ref, widget.workspaceRoot, onGranted: _refresh),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  child: Text(
                    'Grant',
                    style: TextStyle(fontSize: 11, color: palette.brand, fontWeight: FontWeight.w600),
                  ),
                ),
              ),
          ],
        ),
      );
    }

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

/// Single context-menu row shared by file and directory tiles.
PopupMenuItem<String> _menuItem(String value, IconData icon, String label) {
  return PopupMenuItem<String>(
    value: value,
    child: Row(
      children: [
        Icon(icon, size: 16),
        const SizedBox(width: 8),
        Text(label, style: const TextStyle(fontSize: 13)),
      ],
    ),
  );
}

Future<void> _copyToClipboard(BuildContext context, String text) async {
  await Clipboard.setData(ClipboardData(text: text));
  if (context.mounted) {
    AuraSnackbar.show(context, message: 'Path copied', kind: AuraSnackbarKind.success);
  }
}

/// hostd listings carry absolute paths (`<root>/<name>`), dart:io listings
/// carry workspace-relative ones -- normalize to absolute for copy/reveal.
String _absolutePathOf(String workspaceRoot, String entryPath) {
  if (entryPath.startsWith(workspaceRoot)) return entryPath;
  if (Platform.isWindows && RegExp(r'^[A-Za-z]:').hasMatch(entryPath)) return entryPath;
  final sep = Platform.pathSeparator;
  return '$workspaceRoot$sep${entryPath.replaceAll('/', sep)}';
}

/// Inverse of [_absolutePathOf] for the "copy relative path" action.
String _relativePathOf(String workspaceRoot, String entryPath) {
  final normalized = entryPath.replaceAll('\\', '/');
  final root = workspaceRoot.replaceAll('\\', '/');
  if (normalized.startsWith('$root/')) return normalized.substring(root.length + 1);
  return entryPath;
}

Future<void> _revealInFilesystem(BuildContext context, String absolutePath,
    {required bool isDirectory}) async {
  if (!Platform.isWindows) {
    AuraSnackbar.show(
      context,
      message: 'Reveal in filesystem is only supported on Windows for now',
      kind: AuraSnackbarKind.info,
    );
    return;
  }
  try {
    // `explorer <dir>` opens the folder; `explorer /select,<file>` opens the
    // parent with the file highlighted. Pure argv list, no shell interpolation.
    await Process.start(
      'explorer',
      isDirectory ? [absolutePath] : ['/select,', absolutePath],
      runInShell: true,
    );
  } catch (e) {
    if (context.mounted) {
      AuraSnackbar.show(context, message: 'Could not open filesystem: $e', kind: AuraSnackbarKind.error);
    }
  }
}