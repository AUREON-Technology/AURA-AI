import 'package:flutter/material.dart';
import '../../../../core/design_system/design_system.dart';

const Map<String, IconData> _toolIcons = {
  'calculator': Icons.calculate_outlined,
  'current_datetime': Icons.schedule_outlined,
  'search_knowledge': Icons.search,
  'recall_memory': Icons.psychology_outlined,
  'remember_fact': Icons.bookmark_add_outlined,
  'list_files': Icons.folder_outlined,
  'read_file': Icons.description_outlined,
  'fetch_url': Icons.link,
  'get_weather': Icons.cloud_outlined,
};

String _toolLabel(String name) {
  return name
      .split('_')
      .where((w) => w.isNotEmpty)
      .map((w) => '${w[0].toUpperCase()}${w.substring(1)}')
      .join(' ');
}

/// One chip per tool call from the `tool_calls` SSE frame. Deliberately
/// honest about what `/api/v3/chat/stream` can actually show: there is no
/// separate "tool finished with result X" signal on this path, only the
/// model's inline call intent and the turn's own `done` frame -- so a chip
/// pulses while its message is still streaming and settles (stops pulsing)
/// when the whole turn finishes, not when this specific call "completes".
/// An unrecognised tool name still renders (generic icon + title-cased
/// name) so a newly added backend tool never breaks this widget.
class ToolCallChips extends StatelessWidget {
  final List<Map<String, dynamic>> toolCalls;

  const ToolCallChips({super.key, required this.toolCalls});

  @override
  Widget build(BuildContext context) {
    if (toolCalls.isEmpty) return const SizedBox.shrink();

    return Wrap(
      spacing: AuraSpacing.sm,
      runSpacing: AuraSpacing.sm,
      children: toolCalls.map((call) {
        final name = (call['name'] as String?) ?? 'tool';
        final settled = call['settled'] as bool? ?? true;
        return _ToolCallChip(key: ValueKey('${name}_${call.hashCode}'), name: name, settled: settled);
      }).toList(),
    );
  }
}

class _ToolCallChip extends StatefulWidget {
  final String name;
  final bool settled;

  const _ToolCallChip({super.key, required this.name, required this.settled});

  @override
  State<_ToolCallChip> createState() => _ToolCallChipState();
}

class _ToolCallChipState extends State<_ToolCallChip> with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, duration: const Duration(milliseconds: 900))..repeat(reverse: true);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final icon = _toolIcons[widget.name] ?? Icons.build_outlined;
    final label = _toolLabel(widget.name);
    final reduceMotion = MediaQuery.of(context).disableAnimations;

    final chip = Container(
      padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: 4),
      decoration: BoxDecoration(
        color: palette.surfaceVariant,
        borderRadius: BorderRadius.circular(AuraRadius.pill),
        border: Border.all(color: widget.settled ? palette.border : palette.brand),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: widget.settled ? palette.textSecondary : palette.brandOnSurfaceText),
          const SizedBox(width: 4),
          Text(label, style: TextStyle(fontSize: 12, color: widget.settled ? palette.textSecondary : palette.textPrimary)),
        ],
      ),
    );

    if (widget.settled || reduceMotion) return chip;

    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) => Opacity(opacity: 0.6 + (_controller.value * 0.4), child: child),
      child: chip,
    );
  }
}
