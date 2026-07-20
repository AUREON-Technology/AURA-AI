import 'package:flutter/material.dart';
import '../aura_palette.dart';
import '../tokens.dart';

/// Shimmering placeholder block for loading states. V1 used a bare
/// `CircularProgressIndicator` for every loading state (task lists, health
/// cards, chat history) — a skeleton that echoes the eventual content's
/// shape reads as faster and is the standard pattern in Linear/Notion/etc.
class AuraSkeleton extends StatefulWidget {
  final double height;
  final double? width;
  final BorderRadiusGeometry borderRadius;

  const AuraSkeleton({
    super.key,
    this.height = 16,
    this.width,
    this.borderRadius = const BorderRadius.all(Radius.circular(AuraRadius.sm)),
  });

  @override
  State<AuraSkeleton> createState() => _AuraSkeletonState();
}

class _AuraSkeletonState extends State<AuraSkeleton> with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, duration: const Duration(milliseconds: 1200))..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final reduceMotion = MediaQuery.of(context).disableAnimations;

    if (reduceMotion) {
      return Container(
        height: widget.height,
        width: widget.width,
        decoration: BoxDecoration(color: palette.surfaceVariant, borderRadius: widget.borderRadius),
      );
    }

    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        final t = _controller.value;
        return Container(
          height: widget.height,
          width: widget.width,
          decoration: BoxDecoration(
            borderRadius: widget.borderRadius,
            gradient: LinearGradient(
              begin: Alignment(-1 + t * 2, 0),
              end: Alignment(1 + t * 2, 0),
              colors: [
                palette.surfaceVariant,
                palette.border,
                palette.surfaceVariant,
              ],
            ),
          ),
        );
      },
    );
  }
}

/// Convenience: a card-shaped skeleton, sized to roughly match [AuraCard]'s
/// default padding — use while a card's real content is loading.
class AuraSkeletonCard extends StatelessWidget {
  final double height;
  const AuraSkeletonCard({super.key, this.height = 96});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(AuraSpacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const AuraSkeleton(height: 14, width: 120),
          const SizedBox(height: AuraSpacing.md),
          AuraSkeleton(height: height - 44),
        ],
      ),
    );
  }
}
