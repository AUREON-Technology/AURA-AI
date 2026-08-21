import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/theme/theme.dart';
import '../finance_view_model.dart';

class FinanceTransactionHistory extends ConsumerWidget {
  final AsyncValue<List<FinanceRecord>> transactionsAsync;
  const FinanceTransactionHistory({super.key, required this.transactionsAsync});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final formatCurrency = NumberFormat.simpleCurrency(decimalDigits: 2);
    final vm = ref.read(financeViewModelProvider.notifier);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Recent Activities', style: Theme.of(context).textTheme.titleMedium),
            const Divider(color: Color(0x1F94A3B8), height: 20),
            Expanded(
              child: transactionsAsync.when(
                loading: () => const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo)),
                error: (err, stack) => Text('Error: $err', style: const TextStyle(color: Colors.redAccent)),
                data: (txs) {
                  if (txs.isEmpty) {
                    return Center(
                      child: Text('No activities recorded yet', style: TextStyle(color: AuraTheme.textSecondary.withValues(alpha: 0.5))),
                    );
                  }
                  return ListView.builder(
                    itemCount: txs.length,
                    itemBuilder: (context, index) {
                      final tx = txs[index];
                      final isExpense = tx.type == 'expense';
                      final color = isExpense ? Colors.redAccent : AuraTheme.accentTeal;
                      return Dismissible(
                        key: Key(tx.id),
                        direction: DismissDirection.endToStart,
                        background: Container(
                          alignment: Alignment.centerRight,
                          padding: const EdgeInsets.only(right: 20),
                          decoration: BoxDecoration(
                            color: Colors.redAccent.withValues(alpha: 0.2),
                            borderRadius: BorderRadius.circular(12),
                          ),
                          child: const Icon(Icons.delete, color: Colors.redAccent),
                        ),
                        onDismissed: (direction) async {
                          await vm.deleteTransaction(tx.id);
                          if (!context.mounted) return;
                          ScaffoldMessenger.of(context).showSnackBar(
                            SnackBar(content: Text('Deleted entry: ${tx.description ?? tx.category}')),
                          );
                        },
                        child: Container(
                          margin: const EdgeInsets.only(bottom: 12),
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(
                            color: const Color(0x0DFFFFFF),
                            borderRadius: BorderRadius.circular(12),
                            border: Border.all(color: const Color(0x0F94A3B8)),
                          ),
                          child: Row(
                            children: [
                              Container(
                                padding: const EdgeInsets.all(8),
                                decoration: BoxDecoration(color: color.withValues(alpha: 0.1), shape: BoxShape.circle),
                                child: Icon(isExpense ? Icons.arrow_downward : Icons.arrow_upward, color: color, size: 16),
                              ),
                              const SizedBox(width: 12),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(tx.description ?? tx.category, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                                    const SizedBox(height: 2),
                                    Text(
                                      '${tx.category} • ${DateFormat('yyyy-MM-dd').format(tx.date)}',
                                      style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 11),
                                    ),
                                  ],
                                ),
                              ),
                              Text(
                                '${isExpense ? '-' : '+'}${formatCurrency.format(tx.amount)}',
                                style: TextStyle(fontWeight: FontWeight.bold, color: color, fontSize: 14),
                              ),
                            ],
                          ),
                        ),
                      );
                    },
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
