import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import 'chat_header_action_button.dart';
import 'package:aura_client/l10n/l10n.dart';

bool chatHeaderShowsLiveVoice() {
  return !kIsWeb &&
      (defaultTargetPlatform == TargetPlatform.android ||
          defaultTargetPlatform == TargetPlatform.iOS);
}

/// Compact more-menu for Memory / Live voice on narrow chat headers.
class ChatHeaderOverflowMenu extends StatelessWidget {
  const ChatHeaderOverflowMenu({super.key});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return MenuAnchor(
      builder: (context, controller, child) {
        return ChatHeaderActionButton(
          icon: Icons.more_vert,
          color: palette.textPrimary,
          tooltip: context.l10n.moreOptions,
          onPressed: () {
            if (controller.isOpen) {
              controller.close();
            } else {
              controller.open();
            }
          },
        );
      },
      menuChildren: [
        MenuItemButton(
          onPressed: () => context.go('/memory'),
          child: Text(context.l10n.memoryTitle),
        ),
        if (chatHeaderShowsLiveVoice())
          MenuItemButton(
            onPressed: () => context.push('/voice'),
            child: Text(context.l10n.chatLiveVoice),
          ),
      ],
    );
  }
}
