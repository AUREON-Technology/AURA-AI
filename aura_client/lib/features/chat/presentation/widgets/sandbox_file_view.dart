import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';

/// A file path header over a monospace content block -- for a `write_file` confirmation
/// (whole new content) or an `edit_file` confirmation (rendered as a two-line diff of
/// `find`/`replace` via [SandboxDiffView.fromBeforeAfter] by the caller, not here, since the
/// "which lines changed" framing belongs to the diff view, not this one).
class SandboxFileView extends StatelessWidget {
  const SandboxFileView({
    super.key,
    required this.path,
    required this.content,
    this.maxLines = 80,
  });

  final String path;
  final String content;
  final int maxLines;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final lines = content.split('\n');
    final truncated = lines.length > maxLines;
    final visible = truncated ? lines.take(maxLines).toList() : lines;

    return Container(
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
              Icon(Icons.insert_drive_file_outlined, size: 14, color: palette.textSecondary),
              const SizedBox(width: AuraSpacing.xs),
              Expanded(
                child: Text(
                  path,
                  style: TextStyle(
                    fontFamily: 'Courier',
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    color: palette.textSecondary,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          const Divider(height: AuraSpacing.md),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                for (final line in visible)
                  Text(
                    line.isEmpty ? ' ' : line,
                    style: TextStyle(
                      fontFamily: 'Courier',
                      fontSize: 12,
                      color: palette.textPrimary,
                      height: 1.4,
                    ),
                  ),
              ],
            ),
          ),
          if (truncated)
            Padding(
              padding: const EdgeInsets.only(top: AuraSpacing.xs),
              child: Text(
                '… ${lines.length - maxLines} more line(s)',
                style: TextStyle(fontSize: 11, color: palette.textSecondary),
              ),
            ),
        ],
      ),
    );
  }
}
