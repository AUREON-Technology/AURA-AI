import 'package:aura_client/core/locale/text_direction.dart';
import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Renders one file's proposed new content for a pending engineer-agent tool call.
///
/// This is a **content preview, not a true diff** -- the backend arguments this reads from
/// (`github_commit_files`'s `files: {path: content}`) carry only the new text, never a "before"
/// version to diff against. Lines are prefixed like an all-added diff hunk purely as a visual
/// cue; a real diff is future work (either the sandbox's `git_diff` tool returning one, or
/// fetching the "before" content via `github_get_file_contents` client-side first).
class CodePreview extends StatelessWidget {
  const CodePreview({super.key, required this.path, required this.content});

  final String path;
  final String content;

  static const int _maxLines = 60;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final lines = content.split('\n');
    final truncated = lines.length > _maxLines;
    final visible = truncated ? lines.take(_maxLines).toList() : lines;

    return LtrContent(child: Container(
      margin: const EdgeInsets.only(top: AuraSpacing.xs),
      decoration: BoxDecoration(
        color: palette.background,
        borderRadius: BorderRadius.circular(AuraRadius.sm),
        border: Border.all(color: palette.border),
      ),
      padding: const EdgeInsets.all(AuraSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Icon(Icons.description_outlined, size: 14, color: palette.textSecondary),
              const SizedBox(width: AuraSpacing.xs),
              Expanded(
                child: Text(
                  path,
                  style: TextStyle(
                    fontFamily: 'Courier',
                    fontSize: 12,
                    color: palette.textSecondary,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          const Divider(height: AuraSpacing.md),
          for (final line in visible)
            Text(
              '+ $line',
              style: TextStyle(
                fontFamily: 'Courier',
                fontSize: 12,
                color: palette.success,
              ),
            ),
          if (truncated)
            Padding(
              padding: const EdgeInsets.only(top: AuraSpacing.xs),
              child: Text(
                context.l10n.chatMoreLines(lines.length - _maxLines),
                style: TextStyle(fontSize: 12, color: palette.textSecondary),
              ),
            ),
        ],
      ),
    ));
  }
}
