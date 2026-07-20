import 'package:flutter/material.dart';
import '../aura_palette.dart';
import '../tokens.dart';
import 'aura_button.dart';

/// Standard empty/error state — icon, message, optional action. V1's empty
/// states were a one-line grey `Text` centered in a `SizedBox`, which reads
/// as broken rather than intentional. This is also the single component
/// that turns an "error" state into a recoverable one via [actionLabel].
class AuraEmptyState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String? message;
  final String? actionLabel;
  final VoidCallback? onAction;

  const AuraEmptyState({
    super.key,
    required this.icon,
    required this.title,
    this.message,
    this.actionLabel,
    this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AuraSpacing.xxl, horizontal: AuraSpacing.lg),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 36, color: palette.textDisabled),
          const SizedBox(height: AuraSpacing.md),
          Text(title, style: Theme.of(context).textTheme.titleSmall, textAlign: TextAlign.center),
          if (message != null) ...[
            const SizedBox(height: AuraSpacing.xs),
            Text(
              message!,
              style: Theme.of(context).textTheme.bodySmall,
              textAlign: TextAlign.center,
            ),
          ],
          if (actionLabel != null && onAction != null) ...[
            const SizedBox(height: AuraSpacing.lg),
            AuraButton(label: actionLabel!, onPressed: onAction, variant: AuraButtonVariant.secondary),
          ],
        ],
      ),
    );
  }
}
