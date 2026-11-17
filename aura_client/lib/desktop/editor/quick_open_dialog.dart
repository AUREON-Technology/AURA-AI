/// Quick open dialog for Desktop IDE.
///
/// Ctrl+P fuzzy file finder.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/workspace/workspace_file_service.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Result of a successful [fuzzyMatch]: the matched candidate, a relevance [score] (higher is
/// better), and the character indices in [text] that matched the query, for highlighting.
class FuzzyMatch {
  const FuzzyMatch(this.text, this.score, this.matchedIndices);

  final String text;
  final int score;
  final List<int> matchedIndices;
}

/// Subsequence fuzzy match: every character of [query] must appear in [text], in order, but not
/// necessarily contiguous (case-insensitive) -- e.g. "epdart" matches "editor/providers/
/// flutter_code_editor_provider.dart". Returns `null` when [query] isn't a subsequence of [text]
/// at all. Scoring rewards consecutive matched characters (tighter grouping reads as a better
/// match than the same letters scattered across the string) and matches starting earlier in
/// [text] (so a filename-prefix match outranks the same letters found deep in a path).
FuzzyMatch? fuzzyMatch(String text, String query) {
  final lowerText = text.toLowerCase();
  final lowerQuery = query.toLowerCase();
  final indices = <int>[];
  var searchFrom = 0;
  var score = 0;
  int? lastMatchIndex;
  for (final queryChar in lowerQuery.runes) {
    final found = lowerText.indexOf(String.fromCharCode(queryChar), searchFrom);
    if (found == -1) return null;
    indices.add(found);
    score += (lastMatchIndex != null && found == lastMatchIndex + 1) ? 10 : 1;
    lastMatchIndex = found;
    searchFrom = found + 1;
  }
  score -= indices.first;
  return FuzzyMatch(text, score, indices);
}

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
        final matches = _files
            .map((f) => fuzzyMatch(f, query))
            .whereType<FuzzyMatch>()
            .toList()
          ..sort((a, b) => b.score.compareTo(a.score));
        _filteredFiles = matches.map((m) => m.text).take(50).toList();
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
                        decoration: InputDecoration(
                          border: InputBorder.none,
                          hintText: context.l10n.editorQuickOpenHint,
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
                      _query.isEmpty ? context.l10n.editorNoFilesFound : context.l10n.editorNoMatches,
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
              child: Text.rich(
                _highlightMatch(
                  file,
                  query,
                  baseStyle: TextStyle(color: palette.textPrimary, fontFamily: 'Courier', fontSize: 13),
                  matchStyle: TextStyle(
                    color: palette.brand,
                    fontFamily: 'Courier',
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                  ),
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

  /// Bolds the characters [fuzzyMatch] actually matched against [query], rather than just the
  /// text unmodified -- re-runs the same matcher `_filterFiles` used to select this item in the
  /// first place, so the highlighted characters are always exactly the ones that made it match.
  InlineSpan _highlightMatch(
    String text,
    String query, {
    required TextStyle baseStyle,
    required TextStyle matchStyle,
  }) {
    final match = query.isEmpty ? null : fuzzyMatch(text, query);
    if (match == null) return TextSpan(text: text, style: baseStyle);
    final matched = match.matchedIndices.toSet();
    final spans = <InlineSpan>[];
    final buffer = StringBuffer();
    bool? bufferIsMatch;
    void flush() {
      if (buffer.isEmpty) return;
      spans.add(TextSpan(text: buffer.toString(), style: bufferIsMatch == true ? matchStyle : baseStyle));
      buffer.clear();
    }

    for (var i = 0; i < text.length; i++) {
      final isMatch = matched.contains(i);
      if (bufferIsMatch != null && bufferIsMatch != isMatch) flush();
      bufferIsMatch = isMatch;
      buffer.write(text[i]);
    }
    flush();
    return TextSpan(children: spans);
  }
}