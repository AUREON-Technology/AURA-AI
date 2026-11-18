import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import '../health_view_model.dart';
import 'package:aura_client/l10n/l10n.dart';

class HealthProgressBar extends StatelessWidget {
  final String title;
  final int current;
  final int target;
  final String unit;
  final Color color;
  final IconData icon;
  final String? customSubtitle;

  const HealthProgressBar({
    super.key,
    required this.title,
    required this.current,
    required this.target,
    required this.unit,
    required this.color,
    required this.icon,
    this.customSubtitle,
  });

  @override
  Widget build(BuildContext context) {
    final pct = goalProgress(current, target);
    final pctText = (pct * 100).toStringAsFixed(0);
    final palette = AuraPalette.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(icon, color: color, size: 18),
            const SizedBox(width: 8),
            Expanded(
              flex: 3,
              child: Text(
                title,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontWeight: FontWeight.w600, fontSize: 13, color: palette.textPrimary),
              ),
            ),
            const SizedBox(width: 8),
            FittedBox(
              fit: BoxFit.scaleDown,
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    customSubtitle ?? '$current / $target $unit',
                    style: TextStyle(color: palette.textSecondary, fontSize: 12),
                  ),
                  const SizedBox(width: 8),
                  Text('$pctText%', style: TextStyle(fontWeight: FontWeight.bold, color: color, fontSize: 13)),
                ],
              ),
            ),
          ],
        ),
        const SizedBox(height: 6),
        ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: LinearProgressIndicator(
            value: pct,
            backgroundColor: color.withValues(alpha: 0.1),
            valueColor: AlwaysStoppedAnimation<Color>(color),
            minHeight: 10,
          ),
        ),
      ],
    );
  }
}

void showHealthLogDialog({
  required BuildContext context,
  required String title,
  required String labelText,
  required String hintText,
  required TextInputType keyboardType,
  required Function(String) onSubmit,
}) {
  final controller = TextEditingController();
  showDialog(
    context: context,
    builder: (context) {
      final palette = AuraPalette.of(context);
      return AlertDialog(
        backgroundColor: palette.surface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: BorderSide(color: palette.border),
        ),
        title: Text(title, style: TextStyle(color: palette.textPrimary)),
        content: TextField(
          controller: controller,
          keyboardType: keyboardType,
          decoration: InputDecoration(labelText: labelText, hintText: hintText),
          style: TextStyle(color: palette.textPrimary),
          autofocus: true,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(context.l10n.cancel, style: TextStyle(color: palette.textSecondary)),
          ),
          ElevatedButton(
            onPressed: () {
              final input = controller.text.trim();
              if (input.isNotEmpty) onSubmit(input);
              Navigator.pop(context);
            },
            child: Text(context.l10n.commonSave),
          ),
        ],
      );
    },
  ).whenComplete(controller.dispose);
}
