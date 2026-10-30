import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Renders a real unified diff (`+`/`-`/context lines) with the usual green/red/neutral
/// coloring. Used for the sandbox `git_diff` tool's output and for a pending commit
/// confirmation's `diffPreview` (attached server-side only when a sandbox session was active --
/// see `PendingToolCall.diffPreview`). Falls back to [SandboxDiffView.fromBeforeAfter] when only
/// old/new text is available (e.g. `edit_file`'s `find`/`replace` arguments), synthesizing a
/// minimal two-hunk diff rather than a real line-by-line one.
class SandboxDiffView extends StatelessWidget {
  const SandboxDiffView({super.key, required this.diffText, this.maxLines = 300});

  final String diffText;
  final int maxLines;

  factory SandboxDiffView.fromBeforeAfter({
    Key? key,
    required String before,
    required String after,
    int maxLines = 300,
  }) {
    final synthesized = StringBuffer();
    for (final line in before.split('\n')) {
      synthesized.writeln('-$line');
    }
    for (final line in after.split('\n')) {
      synthesized.writeln('+$line');
    }
    return SandboxDiffView(key: key, diffText: synthesized.toString(), maxLines: maxLines);
  }

  Color _lineColor(String line, AuraPalette palette) {
    if (line.startsWith('+') && !line.startsWith('+++')) return palette.success;
    if (line.startsWith('-') && !line.startsWith('---')) return palette.danger;
    if (line.startsWith('@@')) return palette.brand;
    return palette.textSecondary;
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final lines = diffText.split('\n').where((l) => l.isNotEmpty).toList();
    final truncated = lines.length > maxLines;
    final visible = truncated ? lines.take(maxLines).toList() : lines;

    return Container(
      margin: const EdgeInsets.only(top: AuraSpacing.xs),
      width: double.infinity,
      decoration: BoxDecoration(
        color: palette.background,
        borderRadius: BorderRadius.circular(AuraRadius.sm),
        border: Border.all(color: palette.border),
      ),
      padding: const EdgeInsets.all(AuraSpacing.sm),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: ConstrainedBox(
          constraints: BoxConstraints(minWidth: MediaQuery.sizeOf(context).width),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              if (visible.isEmpty)
                Text(context.l10n.sandboxNoChanges, style: TextStyle(color: palette.textSecondary, fontSize: 12))
              else
                for (final line in visible)
                  Text(
                    line,
                    style: TextStyle(
                      fontFamily: 'Courier',
                      fontSize: 12,
                      color: _lineColor(line, palette),
                      height: 1.4,
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
        ),
      ),
    );
  }
}
