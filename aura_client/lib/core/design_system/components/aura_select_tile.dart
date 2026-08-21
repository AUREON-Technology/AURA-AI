import 'package:flutter/material.dart';

import '../aura_palette.dart';
import '../tokens.dart';

/// Overflow-safe selectable row. Prefer this over [ListTile] in sheets and
/// sidebars: Material 3 [ListTile] + [InkSparkle] hides ink, and unbounded
/// [ListTile] columns in a half-height bottom sheet overflow on phones.
class AuraSelectTile extends StatelessWidget {
  final IconData icon;
  final String title;
  final String? subtitle;
  final bool selected;
  final VoidCallback onTap;
  final Widget? trailing;

  const AuraSelectTile({
    super.key,
    required this.icon,
    required this.title,
    this.subtitle,
    this.selected = false,
    required this.onTap,
    this.trailing,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final accent = selected
        ? palette.brandOnSurfaceText
        : palette.textSecondary;

    return Semantics(
      button: true,
      selected: selected,
      label: subtitle == null ? title : '$title. $subtitle',
      child: Material(
        color: selected
            ? palette.brand.withValues(alpha: 0.15)
            : Colors.transparent,
        borderRadius: BorderRadius.circular(AuraRadius.sm),
        child: InkWell(
          borderRadius: BorderRadius.circular(AuraRadius.sm),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: AuraSpacing.md,
              vertical: AuraSpacing.sm,
            ),
            child: Row(
              children: [
                Icon(icon, color: accent, size: 20),
                const SizedBox(width: AuraSpacing.md),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          color: palette.textPrimary,
                          fontWeight: selected
                              ? FontWeight.bold
                              : FontWeight.normal,
                        ),
                      ),
                      if (subtitle != null && subtitle!.isNotEmpty) ...[
                        const SizedBox(height: 2),
                        Text(
                          subtitle!,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            color: palette.textSecondary,
                            fontSize: 12,
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
                ?trailing,
              ],
            ),
          ),
        ),
      ),
    );
  }
}
