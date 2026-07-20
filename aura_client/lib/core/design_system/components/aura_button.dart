import 'package:flutter/material.dart';
import '../aura_palette.dart';
import '../tokens.dart';

enum AuraButtonVariant { primary, secondary, ghost, destructive }

/// Single button component for all four variants used across the app.
/// V1 constructed `ElevatedButton.styleFrom(...)` inline at every call
/// site with slightly different padding/radius each time; this makes the
/// variant (and its accessibility minimum tap target) a one-line choice.
class AuraButton extends StatelessWidget {
  final String label;
  final VoidCallback? onPressed;
  final AuraButtonVariant variant;
  final IconData? icon;
  final bool loading;
  final bool expand;

  const AuraButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.variant = AuraButtonVariant.primary,
    this.icon,
    this.loading = false,
    this.expand = false,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final isDisabled = onPressed == null || loading;

    final (bg, fg, border) = switch (variant) {
      AuraButtonVariant.primary => (palette.brand, palette.onBrand, null),
      AuraButtonVariant.secondary => (palette.surfaceVariant, palette.textPrimary, palette.border),
      AuraButtonVariant.ghost => (Colors.transparent, palette.brandOnSurfaceText, null),
      AuraButtonVariant.destructive => (palette.danger, Colors.white, null),
    };

    final child = loading
        ? SizedBox(
            width: 18,
            height: 18,
            child: CircularProgressIndicator(strokeWidth: 2, color: fg),
          )
        : Row(
            mainAxisSize: expand ? MainAxisSize.max : MainAxisSize.min,
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              if (icon != null) ...[Icon(icon, size: 18, color: fg), const SizedBox(width: AuraSpacing.sm)],
              Text(label, style: TextStyle(color: fg, fontWeight: FontWeight.w600, fontSize: 15)),
            ],
          );

    return Semantics(
      button: true,
      enabled: !isDisabled,
      label: label,
      child: SizedBox(
        width: expand ? double.infinity : null,
        child: Material(
          color: isDisabled ? bg.withValues(alpha: 0.5) : bg,
          borderRadius: BorderRadius.circular(AuraRadius.md),
          child: InkWell(
            borderRadius: BorderRadius.circular(AuraRadius.md),
            onTap: isDisabled ? null : onPressed,
            child: Container(
              constraints: const BoxConstraints(minHeight: 44),
              padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.xl, vertical: AuraSpacing.md),
              decoration: border != null ? BoxDecoration(border: Border.all(color: border), borderRadius: BorderRadius.circular(AuraRadius.md)) : null,
              alignment: Alignment.center,
              child: child,
            ),
          ),
        ),
      ),
    );
  }
}
