import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/finance_repository.dart';

const financeCategories = [
  'Food',
  'Studies',
  'Freelance',
  'Housing',
  'Entertainment',
  'Utilities',
  'Transport',
  'Other',
];

double? parsePositiveAmount(String raw) {
  final amount = double.tryParse(raw.trim());
  if (amount == null || amount <= 0) return null;
  return amount;
}

@immutable
class FinanceComposerState {
  final String transactionType;
  final DateTime selectedDate;
  final String category;

  FinanceComposerState({
    this.transactionType = 'expense',
    DateTime? selectedDate,
    this.category = 'Food',
  }) : selectedDate = selectedDate ?? DateTime.now();

  FinanceComposerState copyWith({
    String? transactionType,
    DateTime? selectedDate,
    String? category,
  }) {
    return FinanceComposerState(
      transactionType: transactionType ?? this.transactionType,
      selectedDate: selectedDate ?? this.selectedDate,
      category: category ?? this.category,
    );
  }
}

class FinanceViewModel extends Notifier<FinanceComposerState> {
  @override
  FinanceComposerState build() => FinanceComposerState();

  void setType(String type) {
    state = state.copyWith(transactionType: type);
  }

  void setCategory(String category) {
    state = state.copyWith(category: category);
  }

  void setDate(DateTime date) {
    state = state.copyWith(selectedDate: date);
  }

  void resetAfterSubmit() {
    state = state.copyWith(selectedDate: DateTime.now());
  }

  Future<String?> submit({
    required String amountRaw,
    required String description,
  }) async {
    final amount = parsePositiveAmount(amountRaw);
    if (amount == null) return 'Please enter a valid positive amount.';
    final category = state.category.trim();
    if (category.isEmpty) return 'Please choose a category.';

    await ref.read(financeRepositoryProvider).addTransaction(
          type: state.transactionType,
          amount: amount,
          category: category,
          description: description.trim().isEmpty ? null : description.trim(),
          date: state.selectedDate,
        );
    resetAfterSubmit();
    return null;
  }

  Future<void> deleteTransaction(String id) {
    return ref.read(financeRepositoryProvider).deleteTransaction(id);
  }
}

final financeViewModelProvider = NotifierProvider<FinanceViewModel, FinanceComposerState>(FinanceViewModel.new);
