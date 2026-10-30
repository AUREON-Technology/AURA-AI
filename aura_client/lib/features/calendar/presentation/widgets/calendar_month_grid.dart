import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/theme/theme.dart';
import '../calendar_view_model.dart';

class CalendarMonthGrid extends ConsumerWidget {
  final Map<String, List<CalendarEvent>> eventsByDay;
  const CalendarMonthGrid({super.key, required this.eventsByDay});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final ui = ref.watch(calendarViewModelProvider);
    final vm = ref.read(calendarViewModelProvider.notifier);
    final monthDays = daysInMonth(ui.currentMonthDate);
    final offset = firstDayOffset(ui.currentMonthDate);
    final totalCells = monthDays + offset;
    final rowCount = (totalCells / 7).ceil();
    final prevMonthDate = DateTime(ui.currentMonthDate.year, ui.currentMonthDate.month - 1, 1);
    final daysInPrev = daysInMonth(prevMonthDate);
    const weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(DateFormat('MMMM yyyy').format(ui.currentMonthDate), style: Theme.of(context).textTheme.titleLarge),
                Row(
                  children: [
                    IconButton(icon: const Icon(Icons.chevron_left, color: Colors.white), onPressed: vm.prevMonth),
                    IconButton(icon: const Icon(Icons.chevron_right, color: Colors.white), onPressed: vm.nextMonth),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 16),
            Row(
              children: weekdays.map((day) {
                return Expanded(
                  child: Center(
                    child: Text(
                      day,
                      style: const TextStyle(color: AuraTheme.textSecondary, fontWeight: FontWeight.bold, fontSize: 12),
                    ),
                  ),
                );
              }).toList(),
            ),
            const SizedBox(height: 8),
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
                      final prevDay = daysInPrev - offset + cellIndex + 1;
                      return _FadedDay(label: '$prevDay');
                    }
                    if (dayNum > monthDays) {
                      return _FadedDay(label: '${dayNum - monthDays}');
                    }
                    final cellDate = DateTime(ui.currentMonthDate.year, ui.currentMonthDate.month, dayNum);
                    final isSelected = cellDate.year == ui.selectedDate.year &&
                        cellDate.month == ui.selectedDate.month &&
                        cellDate.day == ui.selectedDate.day;
                    final now = DateTime.now();
                    final isToday = cellDate.year == now.year && cellDate.month == now.month && cellDate.day == now.day;
                    final hasEvents = eventsByDay.containsKey(dayKey(cellDate));
                    return Expanded(
                      child: GestureDetector(
                        onTap: () => vm.selectDate(cellDate),
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
                              color: isToday && !isSelected ? AuraTheme.accentIndigo : Colors.transparent,
                              width: 1,
                            ),
                          ),
                          child: Padding(
                            padding: const EdgeInsets.symmetric(vertical: 10.0),
                            child: Column(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Text(
                                  '$dayNum',
                                  style: TextStyle(
                                    color: isSelected ? Colors.white : palette.textPrimary,
                                    fontWeight: isSelected || isToday ? FontWeight.bold : FontWeight.normal,
                                    fontSize: 14,
                                  ),
                                ),
                                const SizedBox(height: 4),
                                Container(
                                  width: 5,
                                  height: 5,
                                  decoration: BoxDecoration(
                                    color: hasEvents ? (isSelected ? Colors.white : AuraTheme.accentIndigo) : Colors.transparent,
                                    shape: BoxShape.circle,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                    );
                  }),
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}

class _FadedDay extends StatelessWidget {
  final String label;
  const _FadedDay({required this.label});

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Opacity(
        opacity: 0.25,
        child: Center(
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 12.0),
            child: Text(label, style: const TextStyle(fontSize: 14)),
          ),
        ),
      ),
    );
  }
}
