import 'package:flutter/material.dart';
import '../aura_palette.dart';
import '../tokens.dart';

class AuraListTile extends StatelessWidget {
  final IconData icon;
  final Color? iconColor;
  final String title;
  final String? subtitle;
  final VoidCallback? onTap;
  final Widget? trailing;

  /// Drawn as the open item of a master list (web master-detail layout).
  final bool selected;

  const AuraListTile({
    super.key,
    required this.icon,
    required this.title,
    this.iconColor,
    this.subtitle,
    this.onTap,
    this.trailing,
    this.selected = false,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final color = iconColor ?? palette.brandOnSurfaceText;
    return Semantics(
      button: onTap != null,
      label: subtitle == null ? title : '$title. $subtitle',
      selected: selected,
      child: InkWell(
        onTap: onTap,
        child: Ink(
          color: selected ? palette.brand.withValues(alpha: 0.10) : null,
          child: Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: AuraSpacing.lg,
              vertical: AuraSpacing.md,
            ),
            child: Row(
              children: [
                CircleAvatar(
                  backgroundColor: color.withValues(alpha: 0.12),
                  radius: 18,
                  child: Icon(icon, color: color, size: 18),
                ),
                const SizedBox(width: AuraSpacing.md),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        title,
                        style: Theme.of(context).textTheme.titleSmall,
                      ),
                      if (subtitle != null) ...[
                        const SizedBox(height: 2),
                        Text(
                          subtitle!,
                          style: Theme.of(context).textTheme.bodySmall,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ],
                    ],
                  ),
                ),
                trailing ??
                    Icon(Icons.chevron_right, color: palette.textDisabled),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
