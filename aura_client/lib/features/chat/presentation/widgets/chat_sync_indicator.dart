import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';

import '../../../sync/sync_engine.dart';
import '../chat_view_model.dart';

class ChatSyncIndicator extends ConsumerWidget {
  final bool isMobile;
  const ChatSyncIndicator({super.key, required this.isMobile});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final syncState = ref.watch(syncEngineProvider);

    late final Color indicatorColor;
    late final String statusText;
    late final IconData syncIcon;
    final isSyncing = syncState == SyncState.syncing;

    switch (syncState) {
      case SyncState.synced:
        indicatorColor = palette.success;
        statusText = 'Synced';
        syncIcon = Icons.sync;
      case SyncState.syncing:
        indicatorColor = palette.warning;
        statusText = 'Syncing';
        syncIcon = Icons.sync;
      case SyncState.offline:
        indicatorColor = palette.textDisabled;
        statusText = 'Offline';
        syncIcon = Icons.sync_disabled;
      case SyncState.error:
        indicatorColor = palette.danger;
        statusText = 'Error';
        syncIcon = Icons.sync_problem;
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.sm, vertical: 4),
      decoration: BoxDecoration(
        color: palette.surfaceVariant,
        border: Border.all(color: palette.border),
        borderRadius: BorderRadius.circular(AuraRadius.pill),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(width: 8, height: 8, decoration: BoxDecoration(color: indicatorColor, shape: BoxShape.circle)),
          if (!isMobile) ...[
            const SizedBox(width: AuraSpacing.xs),
            Text(statusText, style: TextStyle(fontSize: 11, color: palette.textSecondary, fontWeight: FontWeight.w500)),
          ],
          const SizedBox(width: 4),
          isSyncing
              ? SizedBox(
                  width: 14,
                  height: 14,
                  child: CircularProgressIndicator(strokeWidth: 1.5, valueColor: AlwaysStoppedAnimation<Color>(palette.warning)),
                )
              : IconButton(
                  tooltip: 'Sync Now',
                  onPressed: isSyncing
                      ? null
                      : () => ref.read(chatViewModelProvider.notifier).synchronize(),
                  style: IconButton.styleFrom(
                    minimumSize: const Size(28, 28),
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    padding: EdgeInsets.zero,
                    visualDensity: VisualDensity.compact,
                  ),
                  icon: Icon(syncIcon, color: indicatorColor, size: 14),
                ),
        ],
      ),
    );
  }
}
