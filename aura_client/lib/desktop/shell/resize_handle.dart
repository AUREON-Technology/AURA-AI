/// Draggable divider between two panels in the Desktop IDE shell.
///
/// `DesktopShell` previously rendered plain `VerticalDivider`s between the
/// sidebars and the main workspace, and no divider at all above the bottom
/// panel -- so despite `LayoutNotifier` already exposing
/// `setPrimarySidebarWidth`/`setSecondarySidebarWidth`/`setBottomPanelHeight`,
/// nothing in the UI ever called them (Phase 0 desktop audit, 2026-09-25).
/// This widget is the missing piece: a thin divider widened into a real
/// hit-test/hover target that reports drag deltas back to the caller.
library;

import 'package:flutter/material.dart';
import 'package:aura_client/core/design_system/design_system.dart';

enum ResizeHandleAxis {
  /// Divider is a vertical line; dragging it left/right resizes a width.
  horizontal,

  /// Divider is a horizontal line; dragging it up/down resizes a height.
  vertical,
}

class ResizeHandle extends StatefulWidget {
  const ResizeHandle({
    super.key,
    required this.axis,
    required this.onDelta,
    this.onDragEnd,
  });

  final ResizeHandleAxis axis;

  /// Called on every drag update with the raw pointer delta -- `dx` for
  /// [ResizeHandleAxis.horizontal], `dy` for [ResizeHandleAxis.vertical].
  /// The caller applies its own sign and clamping: a handle to the left of
  /// the panel it resizes adds the delta, one to the right or below
  /// subtracts it.
  final ValueChanged<double> onDelta;

  /// Called once when a drag gesture ends, so the caller can persist the
  /// final size instead of writing on every frame of movement.
  final VoidCallback? onDragEnd;

  @override
  State<ResizeHandle> createState() => _ResizeHandleState();
}

class _ResizeHandleState extends State<ResizeHandle> {
  bool _hovering = false;
  bool _dragging = false;

  bool get _isHorizontal => widget.axis == ResizeHandleAxis.horizontal;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final highlighted = _hovering || _dragging;
    final lineColor = highlighted ? palette.brand : palette.border;

    final line = Container(
      width: _isHorizontal ? 1 : null,
      height: _isHorizontal ? null : 1,
      color: lineColor,
    );

    return MouseRegion(
      cursor: _isHorizontal ? SystemMouseCursors.resizeColumn : SystemMouseCursors.resizeRow,
      onEnter: (_) => setState(() => _hovering = true),
      onExit: (_) => setState(() => _hovering = false),
      child: GestureDetector(
        behavior: HitTestBehavior.translucent,
        onHorizontalDragStart: _isHorizontal ? (_) => setState(() => _dragging = true) : null,
        onHorizontalDragUpdate: _isHorizontal ? (details) => widget.onDelta(details.delta.dx) : null,
        onHorizontalDragEnd: _isHorizontal ? (_) => _endDrag() : null,
        onHorizontalDragCancel: _isHorizontal ? _endDrag : null,
        onVerticalDragStart: _isHorizontal ? null : (_) => setState(() => _dragging = true),
        onVerticalDragUpdate: _isHorizontal ? null : (details) => widget.onDelta(details.delta.dy),
        onVerticalDragEnd: _isHorizontal ? null : (_) => _endDrag(),
        onVerticalDragCancel: _isHorizontal ? null : _endDrag,
        child: SizedBox(
          width: _isHorizontal ? 5 : null,
          height: _isHorizontal ? null : 5,
          child: Center(child: line),
        ),
      ),
    );
  }

  void _endDrag() {
    setState(() => _dragging = false);
    widget.onDragEnd?.call();
  }
}
