import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_provider.dart';
import '../data/calendar_repository.dart';

int daysInMonth(DateTime date) {
  final firstDayOfNextMonth = DateTime(date.year, date.month + 1, 1);
  return firstDayOfNextMonth.subtract(const Duration(days: 1)).day;
}

/// Monday = 0 … Sunday = 6
int firstDayOffset(DateTime date) {
  return DateTime(date.year, date.month, 1).weekday - 1;
}

String dayKey(DateTime date) {
  final y = date.year.toString().padLeft(4, '0');
  final m = date.month.toString().padLeft(2, '0');
  final d = date.day.toString().padLeft(2, '0');
  return '$y-$m-$d';
}

@immutable
class CalendarUiState {
  final DateTime selectedDate;
  final DateTime currentMonthDate;
  final bool isSyncing;
  final List<String> syncLogs;

  CalendarUiState({
    DateTime? selectedDate,
    DateTime? currentMonthDate,
    this.isSyncing = false,
    this.syncLogs = const [],
  })  : selectedDate = selectedDate ?? DateTime.now(),
        currentMonthDate = currentMonthDate ?? DateTime.now();

  CalendarUiState copyWith({
    DateTime? selectedDate,
    DateTime? currentMonthDate,
    bool? isSyncing,
    List<String>? syncLogs,
  }) {
    return CalendarUiState(
      selectedDate: selectedDate ?? this.selectedDate,
      currentMonthDate: currentMonthDate ?? this.currentMonthDate,
      isSyncing: isSyncing ?? this.isSyncing,
      syncLogs: syncLogs ?? this.syncLogs,
    );
  }
}

class CalendarViewModel extends Notifier<CalendarUiState> {
  @override
  CalendarUiState build() => CalendarUiState();

  void selectDate(DateTime date) {
    state = state.copyWith(selectedDate: date);
  }

  void nextMonth() {
    final current = state.currentMonthDate;
    state = state.copyWith(currentMonthDate: DateTime(current.year, current.month + 1, 1));
  }

  void prevMonth() {
    final current = state.currentMonthDate;
    state = state.copyWith(currentMonthDate: DateTime(current.year, current.month - 1, 1));
  }

  Future<void> deleteEvent(String id) {
    return ref.read(calendarRepositoryProvider).deleteEvent(id);
  }

  Future<List<String>?> syncGoogleCalendar() async {
    if (state.isSyncing) return null;
    state = state.copyWith(isSyncing: true, syncLogs: const ['Starting synchronization...']);

    final auth = ref.read(authProvider).value;
    final isMock = auth?.id == 'mock_user_123' || auth == null;
    try {
      final logs = await ref.read(calendarRepositoryProvider).syncGoogleCalendar(isMock: isMock);
      state = state.copyWith(isSyncing: false, syncLogs: logs);
      return logs;
    } catch (e) {
      final logs = [...state.syncLogs, 'error: $e'];
      state = state.copyWith(isSyncing: false, syncLogs: logs);
      return logs;
    }
  }
}

final calendarViewModelProvider = NotifierProvider<CalendarViewModel, CalendarUiState>(CalendarViewModel.new);
