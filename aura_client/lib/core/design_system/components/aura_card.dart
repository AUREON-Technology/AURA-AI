import 'package:flutter/material.dart';
import '../aura_palette.dart';
import '../tokens.dart';

/// Standard elevated/glass card. Replaces the copy-pasted
/// `Card(color: AuraTheme.cardBg, ...)` + `Padding` boilerplate repeated in
/// every V1 screen (chat, home, settings, login all hand-rolled their own).
class AuraCard extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;
  final String? semanticLabel;
  final bool glass;

  const AuraCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(AuraSpacing.lg),
    this.onTap,
    this.semanticLabel,
    this.glass = false,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);

    final decoration = BoxDecoration(
      color: glass ? null : palette.surface,
      gradient: glass ? palette.glassGradient : null,
      borderRadius: BorderRadius.circular(AuraRadius.lg),
      border: Border.all(color: palette.border, width: 1),
    );

    final content = Container(
      decoration: decoration,
      child: Padding(padding: padding, child: child),
    );

    return Semantics(
      label: semanticLabel,
      button: onTap != null,
      child: onTap == null
          ? content
          : Material(
              color: Colors.transparent,
              borderRadius: BorderRadius.circular(AuraRadius.lg),
              child: InkWell(
                borderRadius: BorderRadius.circular(AuraRadius.lg),
                onTap: onTap,
                child: content,
              ),
            ),
    );
  }
}
