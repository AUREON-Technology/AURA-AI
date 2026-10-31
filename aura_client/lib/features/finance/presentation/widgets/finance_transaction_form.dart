import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/theme/theme.dart';
import '../finance_view_model.dart';
import 'package:aura_client/l10n/l10n.dart';

class FinanceTransactionForm extends ConsumerStatefulWidget {
  const FinanceTransactionForm({super.key});

  @override
  ConsumerState<FinanceTransactionForm> createState() =>
      _FinanceTransactionFormState();
}

class _FinanceTransactionFormState
    extends ConsumerState<FinanceTransactionForm> {
  final _amountController = TextEditingController();
  final _descriptionController = TextEditingController();

  @override
  void dispose() {
    _amountController.dispose();
    _descriptionController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final composer = ref.watch(financeViewModelProvider);
    final vm = ref.read(financeViewModelProvider.notifier);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'Log Transaction',
              style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15),
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: ChoiceChip(
                    label: const Center(child: Text('Expense')),
                    selected: composer.transactionType == 'expense',
                    onSelected: (val) {
                      if (val) vm.setType('expense');
                    },
                    selectedColor: Colors.redAccent.withValues(alpha: 0.2),
                    checkmarkColor: Colors.redAccent,
                    labelStyle: TextStyle(
                      color: composer.transactionType == 'expense'
                          ? Colors.redAccent
                          : AuraTheme.textSecondary,
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: ChoiceChip(
                    label: const Center(child: Text('Income')),
                    selected: composer.transactionType == 'income',
                    onSelected: (val) {
                      if (val) vm.setType('income');
                    },
                    selectedColor: AuraTheme.accentTeal.withValues(alpha: 0.2),
                    checkmarkColor: AuraTheme.accentTeal,
                    labelStyle: TextStyle(
                      color: composer.transactionType == 'income'
                          ? AuraTheme.accentTeal
                          : AuraTheme.textSecondary,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _amountController,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
              ),
              decoration: const InputDecoration(
                labelText: r'Amount ($)',
                hintText: '0.00',
              ),
              style: const TextStyle(color: Colors.white),
            ),
            const SizedBox(height: 16),
            DropdownButtonFormField<String>(
              isExpanded: true,
              initialValue: financeCategories.contains(composer.category)
                  ? composer.category
                  : 'Other',
              dropdownColor: const Color(0xFF0F0C1B),
              style: const TextStyle(color: Colors.white),
              decoration: const InputDecoration(labelText: 'Category'),
              items: financeCategories.map((cat) {
                return DropdownMenuItem(
                  value: cat,
                  child: Text(
                    cat,
                    overflow: TextOverflow.ellipsis,
                    maxLines: 1,
                  ),
                );
              }).toList(),
              onChanged: (val) {
                if (val != null) vm.setCategory(val);
              },
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _descriptionController,
              decoration: const InputDecoration(
                labelText: 'Description (Optional)',
                hintText: 'e.g. Weekly grocery shopping',
              ),
              style: const TextStyle(color: Colors.white),
            ),
            const SizedBox(height: 16),
            Material(
              type: MaterialType.transparency,
              child: ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(
                  Icons.calendar_month,
                  color: AuraTheme.accentIndigo,
                ),
                title: const Text(
                  'Date',
                  style: TextStyle(
                    fontSize: 13,
                    color: AuraTheme.textSecondary,
                  ),
                ),
                subtitle: Text(
                  DateFormat('yyyy-MM-dd').format(composer.selectedDate),
                  style: const TextStyle(color: Colors.white, fontSize: 14),
                ),
                trailing: IconButton(
                  tooltip: context.l10n.changeDate,
                  icon: const Icon(Icons.edit, color: AuraTheme.textSecondary),
                  onPressed: () async {
                    final date = await showDatePicker(
                      context: context,
                      initialDate: composer.selectedDate,
                      firstDate: DateTime.now().subtract(
                        const Duration(days: 365),
                      ),
                      lastDate: DateTime.now(),
                    );
                    if (date != null) vm.setDate(date);
                  },
                ),
              ),
            ),
            const SizedBox(height: 16),
            ElevatedButton(
              onPressed: () async {
                final error = await vm.submit(
                  amountRaw: _amountController.text,
                  description: _descriptionController.text,
                );
                if (!context.mounted) return;
                if (error != null) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                      content: Text(error),
                      backgroundColor: Colors.redAccent,
                    ),
                  );
                  return;
                }
                _amountController.clear();
                _descriptionController.clear();
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text('Transaction logged successfully!'),
                    backgroundColor: AuraTheme.accentTeal,
                  ),
                );
              },
              style: ElevatedButton.styleFrom(
                backgroundColor: composer.transactionType == 'expense'
                    ? Colors.redAccent
                    : AuraTheme.accentTeal,
              ),
              child: const Text('Submit Ledger Entry'),
            ),
          ],
        ),
      ),
    );
  }
}
