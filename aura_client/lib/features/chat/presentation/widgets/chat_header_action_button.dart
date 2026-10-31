import 'package:flutter/material.dart';

/// Compact header control. Material 3 [IconButton] defaults to a 48px
/// minimum, which is what overflowed the 336px chat header on SM-A366B.
class ChatHeaderActionButton extends StatelessWidget {
  final IconData icon;
  final Color color;
  /// Required: an icon-only control has no other accessible name (audit F-36).
  final String tooltip;
  final VoidCallback onPressed;
  final double iconSize;

  const ChatHeaderActionButton({
    super.key,
    required this.icon,
    required this.color,
    required this.onPressed,
    required this.tooltip,
    this.iconSize = 20,
  });

  static ButtonStyle get compactStyle => IconButton.styleFrom(
        minimumSize: const Size(40, 40),
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
        padding: EdgeInsets.zero,
        visualDensity: VisualDensity.compact,
      );

  @override
  Widget build(BuildContext context) {
    return IconButton(
      tooltip: tooltip,
      onPressed: onPressed,
      style: compactStyle,
      icon: Icon(icon, color: color, size: iconSize),
    );
  }
}
