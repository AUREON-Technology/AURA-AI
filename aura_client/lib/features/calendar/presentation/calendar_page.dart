import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import '../data/calendar_repository.dart';
import 'calendar_view_model.dart';
import 'widgets/calendar_month_grid.dart';
import 'widgets/calendar_schedule_panel.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';
import 'package:aura_client/l10n/l10n.dart';

class CalendarPage extends ConsumerWidget {
  const CalendarPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final eventsAsync = ref.watch(calendarEventsStreamProvider);
    final ui = ref.watch(calendarViewModelProvider);
    final vm = ref.read(calendarViewModelProvider.notifier);
    final palette = AuraPalette.of(context);

    return Scaffold(
      backgroundColor: palette.background,
      body: Container(
        decoration: BoxDecoration(
          gradient: RadialGradient(
            center: const Alignment(0.6, -0.6),
            radius: 1.5,
            colors: [palette.brand.withValues(alpha: 0.12), Colors.transparent],
          ),
        ),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24.0),
            child: eventsAsync.when(
              loading: () => Center(
                child: CircularProgressIndicator(color: palette.brand),
              ),
              error: (err, stack) => Center(
                child: SingleChildScrollView(
                  child: AuraFailureView(failure: AuraFailure.from(err)),
                ),
              ),
              data: (events) {
                final eventsByDay = <String, List<CalendarEvent>>{};
                for (final event in events) {
                  eventsByDay
                      .putIfAbsent(dayKey(event.startTime), () => [])
                      .add(event);
                }
                final selectedDayEvents =
                    eventsByDay[dayKey(ui.selectedDate)] ?? [];

                return LayoutBuilder(
                  builder: (context, constraints) {
                    final isDesktop = constraints.maxWidth > 800;
                    final calendarWidget = CalendarMonthGrid(
                      eventsByDay: eventsByDay,
                    );
                    final scheduleWidget = CalendarSchedulePanel(
                      dayEvents: selectedDayEvents,
                    );
                    final header = _CalendarHeader(
                      isSyncing: ui.isSyncing,
                      hasLogs: ui.syncLogs.isNotEmpty,
                      logs: ui.syncLogs,
                      onSync: () async {
                        final logs = await vm.syncGoogleCalendar();
                        if (logs == null || !context.mounted) return;
                        final hadErrors = logs.any((l) => l.contains('error'));
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(
                            content: Text(
                              hadErrors
                                  ? 'Calendar sync encountered errors.'
                                  : 'Google Calendar sync completed successfully!',
                            ),
                            backgroundColor: hadErrors
                                ? palette.danger
                                : palette.success,
                          ),
                        );
                      },
                    );

                    if (isDesktop) {
                      return Column(
                        children: [
                          header,
                          const SizedBox(height: 24),
                          Expanded(
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Expanded(flex: 3, child: calendarWidget),
                                const SizedBox(width: 24),
                                Expanded(flex: 2, child: scheduleWidget),
                              ],
                            ),
                          ),
                        ],
                      );
                    }
                    return ListView(
                      children: [
                        header,
                        const SizedBox(height: 24),
                        calendarWidget,
                        const SizedBox(height: 24),
                        SizedBox(height: 400, child: scheduleWidget),
                      ],
                    );
                  },
                );
              },
            ),
          ),
        ),
      ),
    );
  }
}

class _CalendarHeader extends StatelessWidget {
  final bool isSyncing;
  final bool hasLogs;
  final List<String> logs;
  final VoidCallback onSync;

  const _CalendarHeader({
    required this.isSyncing,
    required this.hasLogs,
    required this.logs,
    required this.onSync,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Wrap(
      spacing: 12,
      runSpacing: 12,
      crossAxisAlignment: WrapCrossAlignment.center,
      alignment: WrapAlignment.spaceBetween,
      children: [
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Calendar',
              style: Theme.of(
                context,
              ).textTheme.displayLarge?.copyWith(fontSize: 28),
            ),
            const SizedBox(height: 4),
            Text(
              'Manage local schedules and sync with Google',
              style: Theme.of(context).textTheme.bodyMedium,
            ),
          ],
        ),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            if (hasLogs)
              IconButton(
                icon: Icon(
                  Icons.receipt_long,
                  color: palette.textSecondary,
                ),
                tooltip: 'View Sync Logs',
                onPressed: () => _showLogs(context, logs),
              ),
            isSyncing
                ? Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 12,
                    ),
                    decoration: BoxDecoration(
                      color: palette.surfaceVariant,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: palette.border),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: palette.brandOnSurfaceText,
                          ),
                        ),
                        const SizedBox(width: 12),
                        Text('Syncing...', style: TextStyle(fontSize: 14, color: palette.textPrimary)),
                      ],
                    ),
                  )
                : ElevatedButton.icon(
                    onPressed: onSync,
                    icon: const Icon(Icons.sync),
                    label: const Text('Sync Calendar'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: palette.brand,
                      foregroundColor: palette.onBrand,
                    ),
                  ),
          ],
        ),
      ],
    );
  }

  void _showLogs(BuildContext context, List<String> logs) {
    showModalBottomSheet(
      context: context,
      backgroundColor: AuraPalette.of(context).background,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (context) {
        final palette = AuraPalette.of(context);
        return SizedBox(
          height: MediaQuery.sizeOf(context).height * 0.5,
          child: Container(
            padding: const EdgeInsets.all(24),
            decoration: BoxDecoration(
              color: palette.surface,
              borderRadius: const BorderRadius.vertical(
                top: Radius.circular(24),
              ),
              border: Border.all(color: palette.border),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        'Sync Activity Log',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                    ),
                    IconButton(
                      tooltip: context.l10n.close,
                      icon: Icon(Icons.close, color: palette.textPrimary),
                      onPressed: () => Navigator.pop(context),
                    ),
                  ],
                ),
                Divider(color: palette.border, height: 24),
                Expanded(
                  child: logs.isEmpty
                      ? Center(
                          child: Text(
                            'No logs recorded yet. Press sync to begin.',
                            style: TextStyle(color: palette.textSecondary),
                          ),
                        )
                      : ListView.builder(
                          itemCount: logs.length,
                          itemBuilder: (context, index) {
                            final log = logs[index];
                            var logIcon = Icons.info_outline;
                            var logColor = palette.textSecondary;
                            if (log.toLowerCase().contains('error') ||
                                log.toLowerCase().contains('failed')) {
                              logIcon = Icons.error_outline;
                              logColor = palette.danger;
                            } else if (log.toLowerCase().contains('success')) {
                              logIcon = Icons.check_circle_outline;
                              logColor = palette.success;
                            } else if (log.toLowerCase().contains('import') ||
                                log.toLowerCase().contains('upload')) {
                              logIcon = Icons.cloud_sync_outlined;
                              logColor = palette.brandOnSurfaceText;
                            }
                            return Padding(
                              padding: const EdgeInsets.symmetric(
                                vertical: 6.0,
                              ),
                              child: Row(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Icon(logIcon, color: logColor, size: 18),
                                  const SizedBox(width: 12),
                                  Expanded(
                                    child: Text(
                                      log,
                                      style: TextStyle(
                                        color: logColor,
                                        fontSize: 13,
                                        fontFamily: 'Courier',
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                            );
                          },
                        ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
