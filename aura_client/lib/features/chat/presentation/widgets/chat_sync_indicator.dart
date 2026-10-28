/// Chat's sync pill.
///
/// The rendering moved to [AuraSyncStatusView] in `core/sync/` so every other
/// surface can show the same state — before, a change sitting unsynced was
/// visible in chat and nowhere else. This stays as the widget chat's header
/// mounts, and supplies the one chat-specific part: which notifier a manual
/// "Sync now" should drive.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/sync/sync_status_view.dart';

import '../chat_view_model.dart';

class ChatSyncIndicator extends ConsumerWidget {
  final bool isMobile;
  const ChatSyncIndicator({super.key, required this.isMobile});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return AuraSyncStatusView(
      showLabel: !isMobile,
      onSyncNow: () => ref.read(chatViewModelProvider.notifier).synchronize(),
    );
  }
}
