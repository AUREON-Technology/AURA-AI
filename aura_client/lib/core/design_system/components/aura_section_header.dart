import 'package:flutter/material.dart';
import '../aura_palette.dart';
import '../tokens.dart';

/// Title + optional trailing action, used to head every card/section.
/// Replaces the `Row(mainAxisAlignment: spaceBetween, children: [Text(...),
/// TextButton(...)])` block duplicated in nearly every V1 card builder.
class AuraSectionHeader extends StatelessWidget {
  final String title;
  final String? actionLabel;
  final VoidCallback? onAction;

  const AuraSectionHeader({
    super.key,
    required this.title,
    this.actionLabel,
    this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Row(
      children: [
        Expanded(
          child: Text(
            title,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: Theme.of(context).textTheme.titleSmall,
          ),
        ),
        if (actionLabel != null && onAction != null)
          Semantics(
            button: true,
            label: actionLabel,
            child: InkWell(
              onTap: onAction,
              borderRadius: BorderRadius.circular(AuraRadius.sm),
              child: Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: AuraSpacing.sm,
                  vertical: AuraSpacing.xs,
                ),
                child: Text(
                  actionLabel!,
                  style: TextStyle(
                    color: palette.brandOnSurfaceText,
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}
