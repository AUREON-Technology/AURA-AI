import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/design_system/design_system.dart';
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
          final palette = AuraPalette.of(context);
          return AlertDialog(
            backgroundColor: palette.surface,
            surfaceTintColor: Colors.transparent,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(20),
              side: BorderSide(color: palette.border),
            ),
            title: Text(
              context.l10n.calendarCreateEventTitle,
              style: TextStyle(color: palette.textPrimary),
            ),
            content: SingleChildScrollView(
              child: SizedBox(
                width: 400,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    TextField(
                      controller: titleController,
                      decoration: InputDecoration(
                        labelText: context.l10n.calendarEventTitleLabel,
                        hintText: context.l10n.calendarEventTitleHint,
                      ),
                      style: TextStyle(color: palette.textPrimary),
                    ),
                    const SizedBox(height: 16),
                    TextField(
                      controller: descController,
                      maxLines: 3,
                      decoration: InputDecoration(
                        labelText: context.l10n.calendarEventDescriptionLabel,
                        hintText: context.l10n.calendarEventDescriptionHint,
                      ),
                      style: TextStyle(color: palette.textPrimary),
                    ),
                    const SizedBox(height: 16),
                    Material(
                      type: MaterialType.transparency,
                      child: ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: Icon(
                          Icons.access_time,
                          color: palette.brandOnSurfaceText,
                        ),
                        title: Text(
                          context.l10n.calendarStartTime,
                          style: const TextStyle(fontSize: 14),
                        ),
                        subtitle: Text(
                          DateFormat('yyyy-MM-dd HH:mm').format(startDate),
                          style: TextStyle(
                            color: palette.textPrimary,
                            fontSize: 13,
                          ),
                        ),
                        trailing: IconButton(
                          tooltip: context.l10n.eventChangeStart,
                          icon: Icon(
                            Icons.edit,
                            color: palette.textSecondary,
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
                        leading: Icon(
                          Icons.access_time_filled,
                          color: palette.warning,
                        ),
                        title: Text(
                          context.l10n.calendarEndTime,
                          style: const TextStyle(fontSize: 14),
                        ),
                        subtitle: Text(
                          DateFormat('yyyy-MM-dd HH:mm').format(endDate),
                          style: TextStyle(
                            color: palette.textPrimary,
                            fontSize: 13,
                          ),
                        ),
                        trailing: IconButton(
                          tooltip: context.l10n.eventChangeEnd,
                          icon: Icon(
                            Icons.edit,
                            color: palette.textSecondary,
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
                child: Text(
                  context.l10n.cancel,
                  style: TextStyle(color: palette.textSecondary),
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
                      SnackBar(
                        content: Text(context.l10n.calendarEventCreated),
                        backgroundColor: palette.success,
                      ),
                    );
                  }
                },
                child: Text(context.l10n.calendarCreate),
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
