import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import '../calendar_view_model.dart';
import 'package:aura_client/l10n/l10n.dart';

class CalendarMonthGrid extends ConsumerWidget {
  final Map<String, List<CalendarEvent>> eventsByDay;
  const CalendarMonthGrid({super.key, required this.eventsByDay});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final ui = ref.watch(calendarViewModelProvider);
    final vm = ref.read(calendarViewModelProvider.notifier);
    final monthDays = daysInMonth(ui.currentMonthDate);
    final localizations = MaterialLocalizations.of(context);
    final firstDay = localizations.firstDayOfWeekIndex;
    final offset = firstDayOffset(ui.currentMonthDate, firstDayOfWeekIndex: firstDay);
    final totalCells = monthDays + offset;
    final rowCount = (totalCells / 7).ceil();
    final prevMonthDate = DateTime(ui.currentMonthDate.year, ui.currentMonthDate.month - 1, 1);
    final daysInPrev = daysInMonth(prevMonthDate);
    // Day names in the language on screen, starting on the locale's first day of the week.
    // 2023-01-01 was a Sunday (index 0). Arabic has no short day names, and its full ones do not
    // fit seven columns on a phone, so a long name falls back to Material's narrow label.
    final weekdays = List.generate(7, (i) {
      final index = (firstDay + i) % 7;
      final name = DateFormat.E().format(DateTime(2023, 1, 1 + index));
      return name.length > 3 ? localizations.narrowWeekdays[index] : name;
    });

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(DateFormat.yMMMM().format(ui.currentMonthDate), style: Theme.of(context).textTheme.titleLarge),
                Row(
                  children: [
                    IconButton(
                      tooltip: context.l10n.calendarPreviousMonth,
                      icon: Icon(Icons.chevron_left, color: palette.textPrimary),
                      onPressed: vm.prevMonth,
                    ),
                    IconButton(
                      tooltip: context.l10n.calendarNextMonth,
                      icon: Icon(Icons.chevron_right, color: palette.textPrimary),
                      onPressed: vm.nextMonth,
                    ),
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
                      style: TextStyle(color: palette.textSecondary, fontWeight: FontWeight.bold, fontSize: 12),
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
                            gradient: isSelected ? palette.brandGradient : null,
                            color: isSelected
                                ? null
                                : isToday
                                    ? palette.brand.withValues(alpha: 0.20)
                                    : Colors.transparent,
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(
                              color: isToday && !isSelected ? palette.brand : Colors.transparent,
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
                                    color: isSelected ? palette.onBrand : palette.textPrimary,
                                    fontWeight: isSelected || isToday ? FontWeight.bold : FontWeight.normal,
                                    fontSize: 14,
                                  ),
                                ),
                                const SizedBox(height: 4),
                                Container(
                                  width: 5,
                                  height: 5,
                                  decoration: BoxDecoration(
                                    color: hasEvents ? (isSelected ? palette.onBrand : palette.brandOnSurfaceText) : Colors.transparent,
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
