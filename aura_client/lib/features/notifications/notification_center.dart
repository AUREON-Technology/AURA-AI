import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import '../../core/database/database.dart';
import '../../core/database/database_provider.dart';
import '../../core/design_system/design_system.dart';
import '../../l10n/generated/app_localizations.dart';

/// Notification Center (Doc/V2_BLUEPRINT.md §3/§13 Phase 2) — surfaces
/// real system events (AI failovers, sync/request errors) from the
/// `TelemetryLogs` table that V1 collected but never showed the user
/// anywhere except a raw analytics chart. Deliberately NOT populated with
/// placeholder content: until Phase 3's AURA Insights engine exists, this
/// only shows genuine failure/failover telemetry, because shipping a bell
/// icon that always reads "0" or shows fake items is worse than no bell.
class AppNotification {
  final String id;
  final String title;
  final String message;
  final DateTime createdAt;
  final bool isError;

  const AppNotification({
    required this.id,
    required this.title,
    required this.message,
    required this.createdAt,
    required this.isError,
  });

  static AppNotification? fromTelemetry(TelemetryLog log) {
    if (log.eventType == 'failover') {
      return AppNotification(
        id: log.id,
        title: 'Server inference failover',
        message:
            'AURA could not complete the request on ${log.modelUsed} and recorded a failover.',
        createdAt: log.createdAt,
        isError: false,
      );
    }
    if (log.eventType == 'error' ||
        (log.errorMessage != null && log.errorMessage!.isNotEmpty)) {
      return AppNotification(
        id: log.id,
        title: 'Request failed',
        message:
            log.errorMessage ?? 'An AI request failed on ${log.modelUsed}.',
        createdAt: log.createdAt,
        isError: true,
      );
    }
    return null; // ordinary successful requests aren't notification-worthy
  }
}

final notificationsProvider = StreamProvider.autoDispose<List<AppNotification>>(
  (ref) {
    final db = ref.watch(databaseProvider);
    return db.watchTelemetry().map(
      (logs) => logs
          .map(AppNotification.fromTelemetry)
          .whereType<AppNotification>()
          .toList(),
    );
  },
);

final notificationsLastReadProvider = FutureProvider.autoDispose<DateTime?>((
  ref,
) {
  final db = ref.watch(databaseProvider);
  return db.getNotificationsLastRead();
});

final unreadNotificationCountProvider = Provider.autoDispose<int>((ref) {
  final notifications = ref.watch(notificationsProvider).value ?? const [];
  final lastRead = ref.watch(notificationsLastReadProvider).value;
  if (lastRead == null) return notifications.length;
  return notifications.where((n) => n.createdAt.isAfter(lastRead)).length;
});

class NotificationBellIcon extends ConsumerWidget {
  const NotificationBellIcon({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final unread = ref.watch(unreadNotificationCountProvider);
    final l10n = AppLocalizations.of(context)!;

    return Semantics(
      label: unread > 0
          ? l10n.unreadNotifications(unread)
          : l10n.notificationsTooltip,
      button: true,
      child: InkWell(
        borderRadius: BorderRadius.circular(AuraRadius.pill),
        onTap: () => showNotificationCenter(context, ref),
        child: Padding(
          padding: const EdgeInsets.all(AuraSpacing.sm),
          child: Stack(
            clipBehavior: Clip.none,
            children: [
              Icon(Icons.notifications_outlined, color: palette.textSecondary),
              if (unread > 0)
                PositionedDirectional(
                  end: -2,
                  top: -2,
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 5,
                      vertical: 1,
                    ),
                    decoration: BoxDecoration(
                      color: palette.danger,
                      borderRadius: BorderRadius.circular(AuraRadius.pill),
                    ),
                    child: Text(
                      unread > 9 ? '9+' : '$unread',
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 10,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

void showNotificationCenter(BuildContext context, WidgetRef ref) {
  showModalBottomSheet(
    context: context,
    backgroundColor: Colors.transparent,
    isScrollControlled: true,
    builder: (context) => const _NotificationSheet(),
  ).whenComplete(() {
    // Mark read on close, not on open, so the badge stays visible while
    // the user is actually looking at the list they just triggered it from.
    ref.read(databaseProvider).setNotificationsLastRead(DateTime.now());
    ref.invalidate(notificationsLastReadProvider);
  });
}

class _NotificationSheet extends ConsumerWidget {
  const _NotificationSheet();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final notificationsAsync = ref.watch(notificationsProvider);

    return Theme(
      data: AuraThemeV2.dark,
      child: Builder(
        builder: (context) {
          final palette = AuraPalette.of(context);
          return SafeArea(
            child: Container(
              constraints: const BoxConstraints(maxHeight: 480),
              margin: const EdgeInsets.all(AuraSpacing.md),
              decoration: BoxDecoration(
                color: palette.surface,
                borderRadius: BorderRadius.circular(AuraRadius.xl),
                border: Border.all(color: palette.border),
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Padding(
                    padding: const EdgeInsets.all(AuraSpacing.lg),
                    child: Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: Text(
                        'Notifications',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                    ),
                  ),
                  const Divider(height: 1),
                  Flexible(
                    child: notificationsAsync.when(
                      data: (items) => items.isEmpty
                          ? const Padding(
                              padding: EdgeInsets.all(AuraSpacing.xl),
                              child: AuraEmptyState(
                                icon: Icons.notifications_none,
                                title: 'Nothing to report',
                                message:
                                    "You'll see AI failovers and request errors here.",
                              ),
                            )
                          : ListView.separated(
                              shrinkWrap: true,
                              padding: const EdgeInsets.symmetric(
                                vertical: AuraSpacing.sm,
                              ),
                              itemCount: items.length,
                              separatorBuilder: (_, _) =>
                                  const Divider(height: 1),
                              itemBuilder: (context, i) =>
                                  _NotificationTile(item: items[i]),
                            ),
                      loading: () => const Padding(
                        padding: EdgeInsets.all(AuraSpacing.xl),
                        child: Center(child: CircularProgressIndicator()),
                      ),
                      error: (_, _) => const Padding(
                        padding: EdgeInsets.all(AuraSpacing.xl),
                        child: AuraEmptyState(
                          icon: Icons.error_outline,
                          title: "Couldn't load notifications",
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}

class _NotificationTile extends StatelessWidget {
  final AppNotification item;
  const _NotificationTile({required this.item});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AuraSpacing.lg,
        vertical: AuraSpacing.md,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            item.isError ? Icons.error_outline : Icons.sync_alt,
            size: 18,
            color: item.isError ? palette.danger : palette.warning,
          ),
          const SizedBox(width: AuraSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(item.title, style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: 2),
                Text(
                  item.message,
                  style: Theme.of(context).textTheme.bodySmall,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 4),
                Text(
                  DateFormat('MMM d, h:mm a').format(item.createdAt),
                  style: TextStyle(color: palette.textDisabled, fontSize: 11),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
