import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import '../data/calendar_repository.dart';
import '../../auth/presentation/auth_provider.dart';
import '../../../core/theme/theme.dart';
import '../../../core/database/database.dart';

class CalendarPage extends ConsumerStatefulWidget {
  const CalendarPage({super.key});

  @override
  ConsumerState<CalendarPage> createState() => _CalendarPageState();
}

class _CalendarPageState extends ConsumerState<CalendarPage> {
  DateTime _selectedDate = DateTime.now();
  DateTime _currentMonthDate = DateTime.now();
  bool _isSyncing = false;
  List<String> _syncLogs = [];

  // Helper: Get days in month
  int _daysInMonth(DateTime date) {
    var firstDayOfNextMonth = DateTime(date.year, date.month + 1, 1);
    var lastDayOfThisMonth = firstDayOfNextMonth.subtract(const Duration(days: 1));
    return lastDayOfThisMonth.day;
  }

  // Helper: Get first day offset (0 = Monday, 6 = Sunday)
  int _firstDayOffset(DateTime date) {
    var firstDay = DateTime(date.year, date.month, 1);
    return firstDay.weekday - 1; // weekday is 1-indexed (Monday = 1)
  }

  void _nextMonth() {
    setState(() {
      _currentMonthDate = DateTime(_currentMonthDate.year, _currentMonthDate.month + 1, 1);
    });
  }

  void _prevMonth() {
    setState(() {
      _currentMonthDate = DateTime(_currentMonthDate.year, _currentMonthDate.month - 1, 1);
    });
  }

  Future<void> _syncCalendar() async {
    setState(() {
      _isSyncing = true;
      _syncLogs = ["Starting synchronization..."];
    });

    final auth = ref.read(authProvider).value;
    final isMock = auth?.id == 'mock_user_123' || auth == null;

    final logs = await ref.read(calendarRepositoryProvider).syncGoogleCalendar(isMock: isMock);

    if (mounted) {
      setState(() {
        _syncLogs = logs;
        _isSyncing = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            logs.any((l) => l.contains("error")) 
                ? "Calendar sync encountered errors." 
                : "Google Calendar sync completed successfully!",
          ),
          backgroundColor: logs.any((l) => l.contains("error")) 
              ? Colors.redAccent 
              : AuraTheme.accentTeal,
        ),
      );
    }
  }

  void _showSyncLogs() {
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF0F0C1B),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (context) {
        return Container(
          padding: const EdgeInsets.all(24),
          decoration: BoxDecoration(
            color: AuraTheme.cardBg,
            borderRadius: const BorderRadius.vertical(top: Radius.circular(24)),
            border: Border.all(color: const Color(0x1F94A3B8)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text("Sync Activity Log", style: Theme.of(context).textTheme.titleLarge),
                  IconButton(
                    icon: const Icon(Icons.close, color: Colors.white),
                    onPressed: () => Navigator.pop(context),
                  ),
                ],
              ),
              const Divider(color: Color(0x1F94A3B8), height: 24),
              Expanded(
                child: _syncLogs.isEmpty
                    ? const Center(
                        child: Text(
                          "No logs recorded yet. Press sync to begin.",
                          style: TextStyle(color: AuraTheme.textSecondary),
                        ),
                      )
                    : ListView.builder(
                        itemCount: _syncLogs.length,
                        itemBuilder: (context, index) {
                          final log = _syncLogs[index];
                          IconData logIcon = Icons.info_outline;
                          Color logColor = AuraTheme.textSecondary;

                          if (log.toLowerCase().contains("error") || log.toLowerCase().contains("failed")) {
                            logIcon = Icons.error_outline;
                            logColor = Colors.redAccent;
                          } else if (log.toLowerCase().contains("success")) {
                            logIcon = Icons.check_circle_outline;
                            logColor = AuraTheme.accentTeal;
                          } else if (log.toLowerCase().contains("import") || log.toLowerCase().contains("upload")) {
                            logIcon = Icons.cloud_sync_outlined;
                            logColor = AuraTheme.accentIndigo;
                          }

                          return Padding(
                            padding: const EdgeInsets.symmetric(vertical: 6.0),
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Icon(logIcon, color: logColor, size: 18),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Text(
                                    log,
                                    style: TextStyle(color: logColor, fontSize: 13, fontFamily: 'Courier'),
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
        );
      },
    );
  }

  void _showAddEventDialog() {
    final titleController = TextEditingController();
    final descController = TextEditingController();
    DateTime startDate = DateTime(_selectedDate.year, _selectedDate.month, _selectedDate.day, 9, 0);
    DateTime endDate = DateTime(_selectedDate.year, _selectedDate.month, _selectedDate.day, 10, 0);

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
              title: const Text("Create Calendar Event", style: TextStyle(color: Colors.white)),
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
                      // Start Time Selector
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.access_time, color: AuraTheme.accentIndigo),
                        title: const Text("Start Time", style: TextStyle(fontSize: 14)),
                        subtitle: Text(DateFormat('yyyy-MM-dd HH:mm').format(startDate),
                            style: const TextStyle(color: Colors.white, fontSize: 13)),
                        trailing: IconButton(
                          icon: const Icon(Icons.edit, color: AuraTheme.textSecondary),
                          onPressed: () async {
                            final date = await showDatePicker(
                              context: context,
                              initialDate: startDate,
                              firstDate: DateTime.now().subtract(const Duration(days: 365)),
                              lastDate: DateTime.now().add(const Duration(days: 365)),
                            );
                            if (date != null) {
                              if (context.mounted) {
                                final time = await showTimePicker(
                                context: context,
                                initialTime: TimeOfDay.fromDateTime(startDate),
                              );
                                if (time != null) {
                                  setDialogState(() {
                                    startDate = DateTime(date.year, date.month, date.day, time.hour, time.minute);
                                    if (endDate.isBefore(startDate)) {
                                      endDate = startDate.add(const Duration(hours: 1));
                                    }
                                  });
                                }
                              }
                            }
                          },
                        ),
                      ),
                      // End Time Selector
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.access_time_filled, color: AuraTheme.accentAmber),
                        title: const Text("End Time", style: TextStyle(fontSize: 14)),
                        subtitle: Text(DateFormat('yyyy-MM-dd HH:mm').format(endDate),
                            style: const TextStyle(color: Colors.white, fontSize: 13)),
                        trailing: IconButton(
                          icon: const Icon(Icons.edit, color: AuraTheme.textSecondary),
                          onPressed: () async {
                            final date = await showDatePicker(
                              context: context,
                              initialDate: endDate,
                              firstDate: DateTime.now().subtract(const Duration(days: 365)),
                              lastDate: DateTime.now().add(const Duration(days: 365)),
                            );
                            if (date != null) {
                              if (context.mounted) {
                                final time = await showTimePicker(
                                context: context,
                                initialTime: TimeOfDay.fromDateTime(endDate),
                              );
                                if (time != null) {
                                  setDialogState(() {
                                    endDate = DateTime(date.year, date.month, date.day, time.hour, time.minute);
                                  });
                                }
                              }
                            }
                          },
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text("Cancel", style: TextStyle(color: AuraTheme.textSecondary)),
                ),
                ElevatedButton(
                  onPressed: () async {
                    if (titleController.text.trim().isEmpty) return;
                    await ref.read(calendarRepositoryProvider).addEvent(
                          title: titleController.text.trim(),
                          description: descController.text.trim().isEmpty ? null : descController.text.trim(),
                          startTime: startDate,
                          endTime: endDate,
                        );
                    if (context.mounted) {
                      Navigator.pop(context);
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(
                          content: Text("Event created successfully!"),
                          backgroundColor: AuraTheme.accentTeal,
                        ),
                      );
                    }
                  },
                  child: const Text("Create"),
                ),
              ],
            );
          },
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final eventsAsync = ref.watch(calendarEventsStreamProvider);

    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment(0.6, -0.6),
            radius: 1.5,
            colors: [
              Color(0xFF1E1B4B),
              Color(0xFF0F0C1B),
            ],
          ),
        ),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24.0),
            child: eventsAsync.when(
              loading: () => const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo)),
              error: (err, stack) => Center(child: Text("Error: $err", style: const TextStyle(color: Colors.redAccent))),
              data: (events) {
                // Categorize events by day
                final eventsByDay = <String, List<CalendarEvent>>{};
                for (final event in events) {
                  final key = DateFormat('yyyy-MM-dd').format(event.startTime);
                  eventsByDay.putIfAbsent(key, () => []).add(event);
                }

                final selectedKey = DateFormat('yyyy-MM-dd').format(_selectedDate);
                final selectedDayEvents = eventsByDay[selectedKey] ?? [];

                return LayoutBuilder(
                  builder: (context, constraints) {
                    final isDesktop = constraints.maxWidth > 800;

                    final calendarWidget = _buildCalendarWidget(eventsByDay);
                    final scheduleWidget = _buildScheduleWidget(selectedDayEvents);

                    if (isDesktop) {
                      return Column(
                        children: [
                          _buildHeader(),
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
                    } else {
                      return ListView(
                        children: [
                          _buildHeader(),
                          const SizedBox(height: 24),
                          calendarWidget,
                          const SizedBox(height: 24),
                          SizedBox(height: 400, child: scheduleWidget),
                        ],
                      );
                    }
                  },
                );
              },
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildHeader() {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              "Calendar",
              style: Theme.of(context).textTheme.displayLarge?.copyWith(fontSize: 28),
            ),
            const SizedBox(height: 4),
            Text(
              "Manage local schedules and sync with Google",
              style: Theme.of(context).textTheme.bodyMedium,
            ),
          ],
        ),
        Row(
          children: [
            if (_syncLogs.isNotEmpty)
              IconButton(
                icon: const Icon(Icons.receipt_long, color: AuraTheme.textSecondary),
                tooltip: "View Sync Logs",
                onPressed: _showSyncLogs,
              ),
            const SizedBox(width: 8),
            _isSyncing
                ? Container(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                    decoration: BoxDecoration(
                      color: AuraTheme.cardBg,
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: const Color(0x1F94A3B8)),
                    ),
                    child: const Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                        ),
                        SizedBox(width: 12),
                        Text("Syncing...", style: TextStyle(fontSize: 14)),
                      ],
                    ),
                  )
                : ElevatedButton.icon(
                    onPressed: _syncCalendar,
                    icon: const Icon(Icons.sync),
                    label: const Text("Sync Calendar"),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AuraTheme.accentIndigo,
                    ),
                  ),
          ],
        ),
      ],
    );
  }

  Widget _buildCalendarWidget(Map<String, List<CalendarEvent>> eventsByDay) {
    final daysInMonth = _daysInMonth(_currentMonthDate);
    final offset = _firstDayOffset(_currentMonthDate);
    final totalCells = daysInMonth + offset;
    final rowCount = (totalCells / 7).ceil();

    final prevMonthDate = DateTime(_currentMonthDate.year, _currentMonthDate.month - 1, 1);
    final daysInPrevMonth = _daysInMonth(prevMonthDate);

    final weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          children: [
            // Calendar Month Navigation
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  DateFormat('MMMM yyyy').format(_currentMonthDate),
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                Row(
                  children: [
                    IconButton(
                      icon: const Icon(Icons.chevron_left, color: Colors.white),
                      onPressed: _prevMonth,
                    ),
                    IconButton(
                      icon: const Icon(Icons.chevron_right, color: Colors.white),
                      onPressed: _nextMonth,
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 16),
            // Weekday labels
            Row(
              children: weekdays.map((day) {
                return Expanded(
                  child: Center(
                    child: Text(
                      day,
                      style: const TextStyle(
                        color: AuraTheme.textSecondary,
                        fontWeight: FontWeight.bold,
                        fontSize: 12,
                      ),
                    ),
                  ),
                );
              }).toList(),
            ),
            const SizedBox(height: 8),
            // Grid of days
            ListView.builder(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              itemCount: rowCount,
              itemBuilder: (context, rowIndex) {
                return Row(
                  children: List.generate(7, (colIndex) {
                    final cellIndex = rowIndex * 7 + colIndex;
                    final dayNum = cellIndex - offset + 1;

                    if (cellIndex < offset) {
                      // Prev Month Day
                      final prevDay = daysInPrevMonth - offset + cellIndex + 1;
                      return Expanded(
                        child: Opacity(
                          opacity: 0.25,
                          child: Center(
                            child: Padding(
                              padding: const EdgeInsets.symmetric(vertical: 12.0),
                              child: Text("$prevDay", style: const TextStyle(fontSize: 14)),
                            ),
                          ),
                        ),
                      );
                    } else if (dayNum > daysInMonth) {
                      // Next Month Day
                      final nextDay = dayNum - daysInMonth;
                      return Expanded(
                        child: Opacity(
                          opacity: 0.25,
                          child: Center(
                            child: Padding(
                              padding: const EdgeInsets.symmetric(vertical: 12.0),
                              child: Text("$nextDay", style: const TextStyle(fontSize: 14)),
                            ),
                          ),
                        ),
                      );
                    } else {
                      // Current Month Day
                      final cellDate = DateTime(_currentMonthDate.year, _currentMonthDate.month, dayNum);
                      final isSelected = cellDate.year == _selectedDate.year &&
                          cellDate.month == _selectedDate.month &&
                          cellDate.day == _selectedDate.day;
                      final isToday = cellDate.year == DateTime.now().year &&
                          cellDate.month == DateTime.now().month &&
                          cellDate.day == DateTime.now().day;

                      final dayKey = DateFormat('yyyy-MM-dd').format(cellDate);
                      final hasEvents = eventsByDay.containsKey(dayKey);

                      return Expanded(
                        child: GestureDetector(
                          onTap: () {
                            setState(() {
                              _selectedDate = cellDate;
                            });
                          },
                          child: Container(
                            margin: const EdgeInsets.symmetric(vertical: 4.0, horizontal: 2.0),
                            decoration: BoxDecoration(
                              gradient: isSelected ? AuraTheme.primaryGradient : null,
                              color: isSelected
                                  ? null
                                  : isToday
                                      ? const Color(0x336366F1)
                                      : Colors.transparent,
                              borderRadius: BorderRadius.circular(10),
                              border: Border.all(
                                color: isToday && !isSelected
                                    ? AuraTheme.accentIndigo
                                    : Colors.transparent,
                                width: 1,
                              ),
                            ),
                            child: Padding(
                              padding: const EdgeInsets.symmetric(vertical: 10.0),
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Text(
                                    "$dayNum",
                                    style: TextStyle(
                                      color: isSelected ? Colors.white : AuraTheme.textPrimary,
                                      fontWeight: isSelected || isToday
                                          ? FontWeight.bold
                                          : FontWeight.normal,
                                      fontSize: 14,
                                    ),
                                  ),
                                  const SizedBox(height: 4),
                                  // Event indicator dot
                                  Container(
                                    width: 5,
                                    height: 5,
                                    decoration: BoxDecoration(
                                      color: hasEvents
                                          ? (isSelected ? Colors.white : AuraTheme.accentIndigo)
                                          : Colors.transparent,
                                      shape: BoxShape.circle,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ),
                        ),
                      );
                    }
                  }),
                );
              },
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildScheduleWidget(List<CalendarEvent> dayEvents) {
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
                      Text(
                        DateFormat('EEEE').format(_selectedDate),
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      Text(
                        DateFormat('MMMM d, yyyy').format(_selectedDate),
                        style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  onPressed: _showAddEventDialog,
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
                            "No events scheduled for today",
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
                                    Text(
                                      event.title,
                                      style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
                                    ),
                                    if (event.description != null) ...[
                                      const SizedBox(height: 2),
                                      Text(
                                        event.description!,
                                        style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 12),
                                      ),
                                    ],
                                    const SizedBox(height: 6),
                                    Row(
                                      children: [
                                        const Icon(Icons.access_time, size: 12, color: AuraTheme.textSecondary),
                                        const SizedBox(width: 4),
                                        Text(
                                          "$startText - $endText",
                                          style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 11),
                                        ),
                                        if (event.googleEventId != null) ...[
                                          const SizedBox(width: 12),
                                          const Icon(Icons.cloud_done_outlined, size: 12, color: AuraTheme.accentTeal),
                                          const SizedBox(width: 4),
                                          const Text(
                                            "Cloud Synced",
                                            style: TextStyle(color: AuraTheme.accentTeal, fontSize: 11),
                                          ),
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
                                      title: const Text("Delete Event"),
                                      content: Text("Are you sure you want to delete '${event.title}'?"),
                                      actions: [
                                        TextButton(
                                          onPressed: () => Navigator.pop(context, false),
                                          child: const Text("Cancel", style: TextStyle(color: AuraTheme.textSecondary)),
                                        ),
                                        ElevatedButton(
                                          onPressed: () => Navigator.pop(context, true),
                                          style: ElevatedButton.styleFrom(backgroundColor: Colors.redAccent),
                                          child: const Text("Delete"),
                                        ),
                                      ],
                                    ),
                                  );

                                  if (confirm == true) {
                                    await ref.read(calendarRepositoryProvider).deleteEvent(event.id);
                                    if (context.mounted) {
                                    ScaffoldMessenger.of(context).showSnackBar(
                                        const SnackBar(
                                          content: Text("Event deleted successfully."),
                                        ),
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
