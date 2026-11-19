import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/features/workspaces/presentation/share_with_team_button.dart';
import 'package:aura_client/core/ai/ai_router/aura_ai_router.dart';
import 'package:aura_client/core/design_system/design_system.dart';

import 'chat_header_action_button.dart';
import 'chat_header_overflow_menu.dart';
import 'chat_sync_indicator.dart';
import 'persona_picker.dart';
import 'package:aura_client/l10n/l10n.dart';

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
              tooltip: context.l10n.chatOpenSessions,
              onPressed: onOpenDrawer,
            ),
            const SizedBox(width: AuraSpacing.xs),
          ],
          _HeaderAvatar(palette: palette),
          const SizedBox(width: AuraSpacing.sm),
          if (isMobile)
            const Flexible(
              child: Align(
                alignment: AlignmentDirectional.centerStart,
                child: PersonaChip(shrink: true),
              ),
            )
          else
            Expanded(
              child: _DesktopTitle(
                palette: palette,
                status: ref.watch(aiRouterStatusProvider),
              ),
            ),
          if (isMobile) ...[
            const SizedBox(width: AuraSpacing.xs),
            const ShareWithTeamButton(),
            const ChatHeaderOverflowMenu(),
          ] else ...[
            const ShareWithTeamButton(),
            ChatHeaderActionButton(
              icon: Icons.psychology_outlined,
              color: palette.warning,
              tooltip: context.l10n.memoryTitle,
              onPressed: () => context.go('/memory'),
            ),
            if (chatHeaderShowsLiveVoice()) ...[
              const SizedBox(width: AuraSpacing.xs),
              ChatHeaderActionButton(
                icon: Icons.graphic_eq,
                color: palette.brand,
                tooltip: context.l10n.chatLiveVoice,
                onPressed: () => context.push('/voice'),
              ),
            ],
            const SizedBox(width: AuraSpacing.xs),
            const PersonaChip(),
          ],
          const SizedBox(width: AuraSpacing.xs),
          ChatSyncIndicator(isMobile: isMobile),
        ],
      ),
    );
  }
}

class _HeaderAvatar extends StatelessWidget {
  final AuraPalette palette;

  const _HeaderAvatar({required this.palette});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 32,
      height: 32,
      decoration: BoxDecoration(borderRadius: BorderRadius.circular(AuraRadius.sm)),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(AuraRadius.sm),
        child: Image.asset(
          'assets/app_icon.png',
          fit: BoxFit.contain,
          errorBuilder: (_, _, _) => Icon(
            Icons.auto_awesome,
            size: 20,
            color: palette.brand,
          ),
        ),
      ),
    );
  }
}

class _DesktopTitle extends StatelessWidget {
  final AuraPalette palette;
  final AIRouterStatus status;

  const _DesktopTitle({required this.palette, required this.status});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          context.l10n.navCopilot,
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
                color: status.isOffline ? palette.warning : palette.success,
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                context.l10n.chatHeaderStatus(
                  status.activeModel,
                  status.status,
                  status.latencyMs.toStringAsFixed(0),
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 11, color: palette.textSecondary),
              ),
            ),
          ],
        ),
      ],
    );
  }
}
