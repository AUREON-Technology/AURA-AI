import 'package:flutter/material.dart';
import '../../../../core/design_system/design_system.dart';
import 'user_facing_citations.dart';

/// Renders the RAG citation labels attached to an assistant message, sourced
/// from the v3 kernel's `citations`/`done` SSE frames
/// (`aura_core/kernel/kernel.py`). These are plain human-readable strings
/// (e.g. "my-doc@v1 (page 4 > Heading)") built by `Chunk.citation()` --
/// there is no score or content snippet available at this layer, unlike the
/// richer cards the standalone Knowledge Center screen shows for its own,
/// separate `/api/v3/knowledge/search` results.
class CitationChips extends StatelessWidget {
  final List<String> citations;

  const CitationChips({super.key, required this.citations});

  @override
  Widget build(BuildContext context) {
    final visible = userFacingCitations(citations);
    if (visible.isEmpty) return const SizedBox.shrink();
    final palette = AuraPalette.of(context);

    return Wrap(
      spacing: AuraSpacing.sm,
      runSpacing: AuraSpacing.sm,
      children: visible.map((citation) {
        return Tooltip(
          message: citation,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: 4),
            decoration: BoxDecoration(
              color: palette.surfaceVariant,
              borderRadius: BorderRadius.circular(AuraRadius.pill),
              border: Border.all(color: palette.border),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.description_outlined, size: 14, color: palette.textSecondary),
                const SizedBox(width: 4),
                ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 180),
                  child: Text(
                    citation,
                    overflow: TextOverflow.ellipsis,
                    maxLines: 1,
                    style: TextStyle(fontSize: 12, color: palette.textSecondary),
                  ),
                ),
              ],
            ),
          ),
        );
      }).toList(),
    );
  }
}
