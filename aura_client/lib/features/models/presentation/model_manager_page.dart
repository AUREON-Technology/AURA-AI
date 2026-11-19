import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/ai/ai_router/aura_ai_router.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

class ModelManagerPage extends ConsumerWidget {
  const ModelManagerPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(aiRouterStatusProvider);
    final palette = AuraPalette.of(context);
    final reachable = !status.isOffline;

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text(context.l10n.engineStatusTitle, style: Theme.of(context).textTheme.headlineMedium),
      ),
      body: ListView(
        padding: const EdgeInsets.all(AuraSpacing.lg),
        children: [
          AuraCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(
                      reachable ? Icons.cloud_done_outlined : Icons.cloud_off_outlined,
                      color: reachable ? palette.success : palette.warning,
                    ),
                    const SizedBox(width: AuraSpacing.md),
                    Expanded(
                      child: Text(status.status, style: Theme.of(context).textTheme.titleSmall),
                    ),
                  ],
                ),
                const SizedBox(height: AuraSpacing.sm),
                Text(
                  status.latencyMs > 0
                      ? context.l10n.modelsLastResponse(status.latencyMs.toStringAsFixed(0))
                      : context.l10n.modelsNoRequestsYet,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                const SizedBox(height: AuraSpacing.md),
                AuraButton(
                  label: context.l10n.modelsCheckAgain,
                  variant: AuraButtonVariant.secondary,
                  onPressed: () => ref.read(aiRouter).probeGateway(),
                ),
              ],
            ),
          ),
          const SizedBox(height: AuraSpacing.lg),
          AuraCard(
            child: Text(
              context.l10n.modelsServerNote,
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        ],
      ),
    );
  }
}
