import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import '../calendar_view_model.dart';
import 'add_event_dialog.dart';
import 'package:aura_client/l10n/l10n.dart';

class CalendarSchedulePanel extends ConsumerWidget {
  final List<CalendarEvent> dayEvents;
  const CalendarSchedulePanel({super.key, required this.dayEvents});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ui = ref.watch(calendarViewModelProvider);
    final vm = ref.read(calendarViewModelProvider.notifier);
    final palette = AuraPalette.of(context);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(DateFormat('EEEE').format(ui.selectedDate), style: Theme.of(context).textTheme.titleMedium),
                      Text(
                        DateFormat('MMMM d, yyyy').format(ui.selectedDate),
                        style: TextStyle(color: palette.textSecondary, fontSize: 13),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  tooltip: context.l10n.eventAdd,
                  onPressed: () => showAddEventDialog(context, ref, ui.selectedDate),
                  icon: Icon(Icons.add, color: palette.onBrand),
                  style: IconButton.styleFrom(
                    backgroundColor: palette.brand,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                  ),
                ),
              ],
            ),
            Divider(color: palette.border, height: 24),
            Expanded(
              child: dayEvents.isEmpty
                  ? Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(Icons.calendar_today, size: 48, color: palette.textSecondary.withValues(alpha: 0.4)),
                          const SizedBox(height: 12),
                          Text(
                            context.l10n.calendarNoEvents,
                            style: TextStyle(color: palette.textSecondary, fontSize: 13),
                            textAlign: TextAlign.center,
                          ),
                        ],
                      ),
                    )
                  : ListView.builder(
                      itemCount: dayEvents.length,
                      itemBuilder: (context, index) {
                        final event = dayEvents[index];
                        final startText = DateFormat('hh:mm a').format(event.startTime);
                        final endText = DateFormat('hh:mm a').format(event.endTime);
                        return Container(
                          margin: const EdgeInsets.only(bottom: 12),
                          padding: const EdgeInsets.all(14),
                          decoration: BoxDecoration(
                            color: palette.surfaceVariant,
                            borderRadius: BorderRadius.circular(12),
                            border: Border.all(color: palette.border),
                          ),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Container(
                                width: 4,
                                height: 45,
                                decoration: BoxDecoration(
                                  gradient: palette.brandGradient,
                                  borderRadius: BorderRadius.circular(2),
                                ),
                              ),
                              const SizedBox(width: 12),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(event.title, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
                                    if (event.description != null) ...[
                                      const SizedBox(height: 2),
                                      Text(event.description!, style: TextStyle(color: palette.textSecondary, fontSize: 12)),
                                    ],
                                    const SizedBox(height: 6),
                                    Row(
                                      children: [
                                        Icon(Icons.access_time, size: 12, color: palette.textSecondary),
                                        const SizedBox(width: 4),
                                        Text('$startText - $endText', style: TextStyle(color: palette.textSecondary, fontSize: 11)),
                                        if (event.googleEventId != null) ...[
                                          const SizedBox(width: 12),
                                          Icon(Icons.cloud_done_outlined, size: 12, color: palette.success),
                                          const SizedBox(width: 4),
                                          Text(context.l10n.calendarCloudSynced, style: TextStyle(color: palette.success, fontSize: 11)),
                                        ],
                                      ],
                                    ),
                                  ],
                                ),
                              ),
                              IconButton(
                                tooltip: context.l10n.eventDelete,
                                icon: Icon(Icons.delete_outline, color: palette.danger, size: 18),
                                onPressed: () async {
                                  final confirm = await showDialog<bool>(
                                    context: context,
                                    builder: (context) => AlertDialog(
                                      backgroundColor: palette.surface,
                                      title: Text(context.l10n.calendarDeleteEventTitle),
                                      content: Text(context.l10n.calendarDeleteEventConfirm(event.title)),
                                      actions: [
                                        TextButton(
                                          onPressed: () => Navigator.pop(context, false),
                                          child: Text(context.l10n.cancel, style: TextStyle(color: palette.textSecondary)),
                                        ),
                                        ElevatedButton(
                                          onPressed: () => Navigator.pop(context, true),
                                          style: ElevatedButton.styleFrom(backgroundColor: palette.danger),
                                          child: Text(context.l10n.commonDelete),
                                        ),
                                      ],
                                    ),
                                  );
                                  if (confirm == true) {
                                    await vm.deleteEvent(event.id);
                                    if (context.mounted) {
                                      ScaffoldMessenger.of(context).showSnackBar(
                                        SnackBar(content: Text(context.l10n.calendarEventDeleted)),
                                      );
                                    }
                                  }
                                },
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
  }
}
