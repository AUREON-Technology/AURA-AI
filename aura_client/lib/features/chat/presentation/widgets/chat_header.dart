import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/ai/ai_router/aura_ai_router.dart';
import 'package:aura_client/core/design_system/design_system.dart';

import 'chat_header_action_button.dart';
import 'chat_sync_indicator.dart';
import 'persona_picker.dart';

class ChatHeader extends ConsumerWidget {
  final bool isMobile;
  final VoidCallback onOpenDrawer;

  const ChatHeader({
    super.key,
    required this.isMobile,
    required this.onOpenDrawer,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final routerStatus = ref.watch(aiRouterStatusProvider);

    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: isMobile ? AuraSpacing.md : AuraSpacing.xl,
        vertical: AuraSpacing.lg,
      ),
      decoration: BoxDecoration(border: Border(bottom: BorderSide(color: palette.border, width: 1))),
      child: Row(
        children: [
          if (isMobile) ...[
            ChatHeaderActionButton(
              icon: Icons.menu,
              color: palette.textPrimary,
              onPressed: onOpenDrawer,
            ),
            const SizedBox(width: AuraSpacing.xs),
          ],
          Container(
            width: 32,
            height: 32,
            decoration: BoxDecoration(borderRadius: BorderRadius.circular(AuraRadius.sm)),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(AuraRadius.sm),
              child: Image.asset('assets/app_icon.png', fit: BoxFit.contain),
            ),
          ),
          const SizedBox(width: AuraSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Copilot',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: palette.textPrimary),
                ),
                Row(
                  children: [
                    Container(
                      width: 8,
                      height: 8,
                      decoration: BoxDecoration(
                        color: routerStatus.isOffline ? palette.warning : palette.success,
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        isMobile
                            ? 'Active • ${routerStatus.activeModel}'
                            : 'Active • ${routerStatus.activeModel} (${routerStatus.status}) • ${routerStatus.latencyMs.toStringAsFixed(0)}ms',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(fontSize: 11, color: palette.textSecondary),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          ChatHeaderActionButton(
            icon: Icons.psychology_outlined,
            color: palette.warning,
            tooltip: 'Memory',
            onPressed: () => context.go('/memory'),
          ),
          const SizedBox(width: AuraSpacing.xs),
          const PersonaChip(shrink: true),
          const SizedBox(width: AuraSpacing.xs),
          ChatSyncIndicator(isMobile: isMobile),
        ],
      ),
    );
  }
}
