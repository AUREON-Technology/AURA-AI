import 'package:flutter/material.dart';
import '../aura_palette.dart';
import '../tokens.dart';
import 'aura_button.dart';

class AuraDialog extends StatelessWidget {
  final String title;
  final Widget child;
  final String? confirmLabel;
  final String? cancelLabel;
  final VoidCallback? onConfirm;
  final bool confirmDestructive;

  const AuraDialog({
    super.key,
    required this.title,
    required this.child,
    this.confirmLabel,
    this.cancelLabel,
    this.onConfirm,
    this.confirmDestructive = false,
  });

  static Future<T?> show<T>({
    required BuildContext context,
    required String title,
    required Widget child,
    String? confirmLabel,
    String? cancelLabel,
    VoidCallback? onConfirm,
    bool confirmDestructive = false,
  }) {
    return showDialog<T>(
      context: context,
      builder: (context) => AuraDialog(
        title: title,
        confirmLabel: confirmLabel,
        cancelLabel: cancelLabel,
        onConfirm: onConfirm,
        confirmDestructive: confirmDestructive,
        child: child,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Dialog(
      backgroundColor: palette.surface,
      insetPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AuraRadius.lg),
        side: BorderSide(color: palette.border),
      ),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 480),
        child: Padding(
          padding: const EdgeInsets.all(AuraSpacing.xl),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(title, style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(height: AuraSpacing.lg),
              Flexible(child: child),
              if (confirmLabel != null || cancelLabel != null) ...[
                const SizedBox(height: AuraSpacing.xl),
                Row(
                  children: [
                    if (cancelLabel != null)
                      Expanded(
                        child: AuraButton(
                          label: cancelLabel!,
                          variant: AuraButtonVariant.secondary,
                          // Each button already sits in an Expanded slot; `expand` lets a
                          // label that does not fit ellipsize instead of overflowing the row.
                          expand: true,
                          onPressed: () => Navigator.of(context).pop(),
                        ),
                      ),
                    if (cancelLabel != null && confirmLabel != null)
                      const SizedBox(width: AuraSpacing.md),
                    if (confirmLabel != null)
                      Expanded(
                        child: AuraButton(
                          label: confirmLabel!,
                          variant: confirmDestructive
                              ? AuraButtonVariant.destructive
                              : AuraButtonVariant.primary,
                          expand: true,
                          onPressed: onConfirm,
                        ),
                      ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
