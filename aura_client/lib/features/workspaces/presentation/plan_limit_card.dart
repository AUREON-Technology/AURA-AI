import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../../core/design_system/design_system.dart';
import '../../../l10n/l10n.dart';

/// What a plan limit says, and the way to more: "See plans" opens the plan page for the active
/// workspace (SaaS P1-F2). Retry is not offered -- the same request meets the same limit.
class PlanLimitCard extends StatelessWidget {
  const PlanLimitCard({super.key, required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return AuraCard(
      key: const Key('plan-limit-card'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(Icons.workspace_premium_outlined, color: palette.brand, size: 20),
          const SizedBox(width: AuraSpacing.sm),
          Expanded(child: Text(text, style: TextStyle(color: palette.textPrimary, height: 1.4))),
        ]),
        const SizedBox(height: AuraSpacing.md),
        AuraButton(label: context.l10n.seePlans, icon: Icons.arrow_forward, onPressed: () => context.push('/plan')),
      ]),
    );
  }
}
