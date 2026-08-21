import 'package:flutter/material.dart';

import 'package:aura_client/core/theme/theme.dart';
import '../health_view_model.dart';

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
                style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
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
                    style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 12),
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
      return AlertDialog(
        backgroundColor: const Color(0xFF0F0C1B),
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: const BorderSide(color: Color(0x1F94A3B8)),
        ),
        title: Text(title, style: const TextStyle(color: Colors.white)),
        content: TextField(
          controller: controller,
          keyboardType: keyboardType,
          decoration: InputDecoration(labelText: labelText, hintText: hintText),
          style: const TextStyle(color: Colors.white),
          autofocus: true,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel', style: TextStyle(color: AuraTheme.textSecondary)),
          ),
          ElevatedButton(
            onPressed: () {
              final input = controller.text.trim();
              if (input.isNotEmpty) onSubmit(input);
              Navigator.pop(context);
            },
            child: const Text('Save'),
          ),
        ],
      );
    },
  ).whenComplete(controller.dispose);
}
