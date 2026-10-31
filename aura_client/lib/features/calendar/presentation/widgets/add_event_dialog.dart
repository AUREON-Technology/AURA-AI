import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/theme/theme.dart';
import '../../data/calendar_repository.dart';
import 'package:aura_client/l10n/l10n.dart';

void showAddEventDialog(
  BuildContext context,
  WidgetRef ref,
  DateTime selectedDate,
) {
  final titleController = TextEditingController();
  final descController = TextEditingController();
  DateTime startDate = DateTime(
    selectedDate.year,
    selectedDate.month,
    selectedDate.day,
    9,
    0,
  );
  DateTime endDate = DateTime(
    selectedDate.year,
    selectedDate.month,
    selectedDate.day,
    10,
    0,
  );

  showDialog(
    context: context,
    builder: (context) {
      return StatefulBuilder(
        builder: (context, setDialogState) {
          return AlertDialog(
            backgroundColor: const Color(0xFF0F0C1B),
            surfaceTintColor: Colors.transparent,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(20),
              side: const BorderSide(color: Color(0x1F94A3B8)),
            ),
            title: const Text(
              'Create Calendar Event',
              style: TextStyle(color: Colors.white),
            ),
            content: SingleChildScrollView(
              child: SizedBox(
                width: 400,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    TextField(
                      controller: titleController,
                      decoration: const InputDecoration(
                        labelText: 'Event Title',
                        hintText: 'e.g. Team Standup',
                      ),
                      style: const TextStyle(color: Colors.white),
                    ),
                    const SizedBox(height: 16),
                    TextField(
                      controller: descController,
                      maxLines: 3,
                      decoration: const InputDecoration(
                        labelText: 'Description (Optional)',
                        hintText: 'Enter event details',
                      ),
                      style: const TextStyle(color: Colors.white),
                    ),
                    const SizedBox(height: 16),
                    Material(
                      type: MaterialType.transparency,
                      child: ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(
                          Icons.access_time,
                          color: AuraTheme.accentIndigo,
                        ),
                        title: const Text(
                          'Start Time',
                          style: TextStyle(fontSize: 14),
                        ),
                        subtitle: Text(
                          DateFormat('yyyy-MM-dd HH:mm').format(startDate),
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 13,
                          ),
                        ),
                        trailing: IconButton(
                          tooltip: context.l10n.eventChangeStart,
                          icon: const Icon(
                            Icons.edit,
                            color: AuraTheme.textSecondary,
                          ),
                          onPressed: () async {
                            final date = await showDatePicker(
                              context: context,
                              initialDate: startDate,
                              firstDate: DateTime.now().subtract(
                                const Duration(days: 365),
                              ),
                              lastDate: DateTime.now().add(
                                const Duration(days: 365),
                              ),
                            );
                            if (date != null && context.mounted) {
                              final time = await showTimePicker(
                                context: context,
                                initialTime: TimeOfDay.fromDateTime(startDate),
                              );
                              if (time != null) {
                                setDialogState(() {
                                  startDate = DateTime(
                                    date.year,
                                    date.month,
                                    date.day,
                                    time.hour,
                                    time.minute,
                                  );
                                  if (endDate.isBefore(startDate)) {
                                    endDate = startDate.add(
                                      const Duration(hours: 1),
                                    );
                                  }
                                });
                              }
                            }
                          },
                        ),
                      ),
                    ),
                    Material(
                      type: MaterialType.transparency,
                      child: ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(
                          Icons.access_time_filled,
                          color: AuraTheme.accentAmber,
                        ),
                        title: const Text(
                          'End Time',
                          style: TextStyle(fontSize: 14),
                        ),
                        subtitle: Text(
                          DateFormat('yyyy-MM-dd HH:mm').format(endDate),
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 13,
                          ),
                        ),
                        trailing: IconButton(
                          tooltip: context.l10n.eventChangeEnd,
                          icon: const Icon(
                            Icons.edit,
                            color: AuraTheme.textSecondary,
                          ),
                          onPressed: () async {
                            final date = await showDatePicker(
                              context: context,
                              initialDate: endDate,
                              firstDate: DateTime.now().subtract(
                                const Duration(days: 365),
                              ),
                              lastDate: DateTime.now().add(
                                const Duration(days: 365),
                              ),
                            );
                            if (date != null && context.mounted) {
                              final time = await showTimePicker(
                                context: context,
                                initialTime: TimeOfDay.fromDateTime(endDate),
                              );
                              if (time != null) {
                                setDialogState(() {
                                  endDate = DateTime(
                                    date.year,
                                    date.month,
                                    date.day,
                                    time.hour,
                                    time.minute,
                                  );
                                });
                              }
                            }
                          },
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text(
                  'Cancel',
                  style: TextStyle(color: AuraTheme.textSecondary),
                ),
              ),
              ElevatedButton(
                onPressed: () async {
                  if (titleController.text.trim().isEmpty) return;
                  await ref
                      .read(calendarRepositoryProvider)
                      .addEvent(
                        title: titleController.text.trim(),
                        description: descController.text.trim().isEmpty
                            ? null
                            : descController.text.trim(),
                        startTime: startDate,
                        endTime: endDate,
                      );
                  if (context.mounted) {
                    Navigator.pop(context);
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(
                        content: Text('Event created successfully!'),
                        backgroundColor: AuraTheme.accentTeal,
                      ),
                    );
                  }
                },
                child: const Text('Create'),
              ),
            ],
          );
        },
      );
    },
  ).whenComplete(() {
    titleController.dispose();
    descController.dispose();
  });
}
