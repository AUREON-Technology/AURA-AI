import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/theme/theme.dart';
import '../calendar_view_model.dart';
import 'add_event_dialog.dart';

class CalendarSchedulePanel extends ConsumerWidget {
  final List<CalendarEvent> dayEvents;
  const CalendarSchedulePanel({super.key, required this.dayEvents});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ui = ref.watch(calendarViewModelProvider);
    final vm = ref.read(calendarViewModelProvider.notifier);

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
                        style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  onPressed: () => showAddEventDialog(context, ref, ui.selectedDate),
                  icon: const Icon(Icons.add, color: Colors.white),
                  style: IconButton.styleFrom(
                    backgroundColor: AuraTheme.accentIndigo,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                  ),
                ),
              ],
            ),
            const Divider(color: Color(0x1F94A3B8), height: 24),
            Expanded(
              child: dayEvents.isEmpty
                  ? Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Icon(Icons.calendar_today, size: 48, color: AuraTheme.textSecondary.withValues(alpha: 0.4)),
                          const SizedBox(height: 12),
                          const Text(
                            'No events scheduled for today',
                            style: TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
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
                            color: const Color(0x0DFFFFFF),
                            borderRadius: BorderRadius.circular(12),
                            border: Border.all(color: const Color(0x0F94A3B8)),
                          ),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Container(
                                width: 4,
                                height: 45,
                                decoration: BoxDecoration(
                                  gradient: AuraTheme.primaryGradient,
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
                                      Text(event.description!, style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 12)),
                                    ],
                                    const SizedBox(height: 6),
                                    Row(
                                      children: [
                                        const Icon(Icons.access_time, size: 12, color: AuraTheme.textSecondary),
                                        const SizedBox(width: 4),
                                        Text('$startText - $endText', style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 11)),
                                        if (event.googleEventId != null) ...[
                                          const SizedBox(width: 12),
                                          const Icon(Icons.cloud_done_outlined, size: 12, color: AuraTheme.accentTeal),
                                          const SizedBox(width: 4),
                                          const Text('Cloud Synced', style: TextStyle(color: AuraTheme.accentTeal, fontSize: 11)),
                                        ],
                                      ],
                                    ),
                                  ],
                                ),
                              ),
                              IconButton(
                                icon: const Icon(Icons.delete_outline, color: Colors.redAccent, size: 18),
                                onPressed: () async {
                                  final confirm = await showDialog<bool>(
                                    context: context,
                                    builder: (context) => AlertDialog(
                                      backgroundColor: const Color(0xFF0F0C1B),
                                      title: const Text('Delete Event'),
                                      content: Text("Are you sure you want to delete '${event.title}'?"),
                                      actions: [
                                        TextButton(
                                          onPressed: () => Navigator.pop(context, false),
                                          child: const Text('Cancel', style: TextStyle(color: AuraTheme.textSecondary)),
                                        ),
                                        ElevatedButton(
                                          onPressed: () => Navigator.pop(context, true),
                                          style: ElevatedButton.styleFrom(backgroundColor: Colors.redAccent),
                                          child: const Text('Delete'),
                                        ),
                                      ],
                                    ),
                                  );
                                  if (confirm == true) {
                                    await vm.deleteEvent(event.id);
                                    if (context.mounted) {
                                      ScaffoldMessenger.of(context).showSnackBar(
                                        const SnackBar(content: Text('Event deleted successfully.')),
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
