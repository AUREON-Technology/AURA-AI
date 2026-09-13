/// Quick open dialog for Desktop IDE.
///
/// Ctrl+P fuzzy file finder.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/workspace/workspace_file_service.dart';

class QuickOpenDialog extends ConsumerStatefulWidget {
  const QuickOpenDialog({super.key, required this.workspaceRoot, required this.onOpenFile});

  final String workspaceRoot;
  final void Function(String relativePath) onOpenFile;

  @override
  ConsumerState<QuickOpenDialog> createState() => _QuickOpenDialogState();
}

class _QuickOpenDialogState extends ConsumerState<QuickOpenDialog> {
  final _controller = TextEditingController();
  String _query = '';
  List<String> _files = const [];
  List<String> _filteredFiles = const [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _loadFiles();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _loadFiles() async {
    setState(() => _loading = true);
    try {
      final files = await walkWorkspaceFiles(widget.workspaceRoot);
      if (!mounted) return;
      setState(() {
        _files = files;
        _filteredFiles = files;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _files = const [];
        _filteredFiles = const [];
        _loading = false;
      });
    }
  }

  void _filterFiles(String query) {
    setState(() {
      _query = query;
      if (query.trim().isEmpty) {
        _filteredFiles = _files;
      } else {
        final lower = query.toLowerCase();
        _filteredFiles = _files
            .where((f) => f.toLowerCase().contains(lower))
            .take(50)
            .toList();
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final ideDensity = context.ideDensity;

    return Dialog(
      alignment: Alignment.topCenter,
      insetPadding: EdgeInsets.only(
        top: 100,
        left: 24,
        right: 24,
        bottom: 24,
      ),
      backgroundColor: Colors.transparent,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: 600, maxHeight: 400),
        child: AuraCard(
          padding: EdgeInsets.zero,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Padding(
                padding: EdgeInsets.all(ideDensity.spacingScale * AuraSpacing.lg),
                child: Row(
                  children: [
                    Icon(Icons.search, color: palette.textSecondary, size: 20),
                    const SizedBox(width: AuraSpacing.md),
                    Expanded(
                      child: TextField(
                        controller: _controller,
                        autofocus: true,
                        onChanged: _filterFiles,
                        style: Theme.of(context).textTheme.bodyLarge,
                        decoration: const InputDecoration(
                          border: InputBorder.none,
                          hintText: 'Type to search files...',
                          isDense: true,
                        ),
                      ),
                    ),
                    Text(
                      'Ctrl+P',
                      style: TextStyle(
                        color: palette.textDisabled,
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
              ),
              const Divider(height: 1),
              if (_loading)
                const Padding(
                  padding: EdgeInsets.all(32),
                  child: Center(child: CircularProgressIndicator()),
                )
              else
                Flexible(
                  child: ListView.builder(
                    shrinkWrap: true,
                    itemCount: _filteredFiles.length,
                    itemBuilder: (context, index) {
                      final file = _filteredFiles[index];
                      return _FileItem(
                        file: file,
                        query: _query,
                        onTap: () {
                          Navigator.of(context).pop();
                          widget.onOpenFile(file);
                        },
                        palette: palette,
                        ideDensity: ideDensity,
                      );
                    },
                  ),
                ),
              if (!_loading && _filteredFiles.isEmpty)
                Padding(
                  padding: EdgeInsets.all(ideDensity.spacingScale * AuraSpacing.lg),
                  child: Center(
                    child: Text(
                      _query.isEmpty ? 'No files found' : 'No matches',
                      style: TextStyle(color: palette.textDisabled),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _FileItem extends StatelessWidget {
  const _FileItem({
    required this.file,
    required this.query,
    required this.onTap,
    required this.palette,
    required this.ideDensity,
  });

  final String file;
  final String query;
  final VoidCallback onTap;
  final AuraPalette palette;
  final AuraIdeDensity ideDensity;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: EdgeInsets.symmetric(
          horizontal: ideDensity.spacingScale * AuraSpacing.lg,
          vertical: ideDensity.spacingScale * AuraSpacing.sm,
        ),
        child: Row(
          children: [
            Icon(
              _getFileIcon(file),
              size: 16,
              color: palette.textSecondary,
            ),
            const SizedBox(width: AuraSpacing.md),
            Expanded(
              child: Text(
                _highlightMatch(file, query),
                style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                  color: palette.textPrimary,
                  fontFamily: 'Courier',
                  fontSize: 13,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
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

  String _highlightMatch(String text, String query) {
    if (query.isEmpty) return text;
    // Simple highlighting - could be enhanced with RichText
    return text;
  }
}