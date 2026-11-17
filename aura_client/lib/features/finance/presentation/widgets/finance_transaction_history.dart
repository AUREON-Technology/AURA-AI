import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import '../finance_view_model.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';
import 'package:aura_client/l10n/l10n.dart';

class FinanceTransactionHistory extends ConsumerWidget {
  final AsyncValue<List<FinanceRecord>> transactionsAsync;
  const FinanceTransactionHistory({super.key, required this.transactionsAsync});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final formatCurrency = NumberFormat.simpleCurrency(locale: 'en_US', decimalDigits: 2);
    final palette = AuraPalette.of(context);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: LayoutBuilder(
          builder: (context, constraints) {
            final bounded = constraints.hasBoundedHeight;
            return Column(
              mainAxisSize: bounded ? MainAxisSize.max : MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(context.l10n.financeRecentActivities, style: Theme.of(context).textTheme.titleMedium),
                Divider(color: palette.border, height: 20),
                if (bounded)
                  Expanded(
                    child: _HistoryBody(
                      transactionsAsync: transactionsAsync,
                      formatCurrency: formatCurrency,
                      shrinkWrap: false,
                    ),
                  )
                else
                  _HistoryBody(
                    transactionsAsync: transactionsAsync,
                    formatCurrency: formatCurrency,
                    shrinkWrap: true,
                  ),
              ],
            );
          },
        ),
      ),
    );
  }
}

class _HistoryBody extends ConsumerWidget {
  final AsyncValue<List<FinanceRecord>> transactionsAsync;
  final NumberFormat formatCurrency;
  final bool shrinkWrap;

  const _HistoryBody({
    required this.transactionsAsync,
    required this.formatCurrency,
    required this.shrinkWrap,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    return transactionsAsync.when(
      loading: () => Padding(
        padding: const EdgeInsets.symmetric(vertical: 24),
        child: Center(child: CircularProgressIndicator(color: palette.brand)),
      ),
      error: (err, stack) => AuraFailureView(
        failure: AuraFailure.from(err),
        compact: true,
      ),
      data: (txs) {
        if (txs.isEmpty) {
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 24),
            child: Center(
              child: Text(
                context.l10n.financeNoActivities,
                style: TextStyle(color: palette.textSecondary),
              ),
            ),
          );
        }
        return ListView.builder(
          shrinkWrap: shrinkWrap,
          physics: shrinkWrap ? const NeverScrollableScrollPhysics() : null,
          itemCount: txs.length,
          itemBuilder: (context, index) {
            final tx = txs[index];
            final isExpense = tx.type == 'expense';
            final color = isExpense ? palette.danger : palette.success;
            return Dismissible(
              key: Key(tx.id),
              direction: DismissDirection.endToStart,
              background: Container(
                alignment: AlignmentDirectional.centerEnd,
                padding: const EdgeInsetsDirectional.only(end: 20),
                decoration: BoxDecoration(
                  color: palette.danger.withValues(alpha: 0.2),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Icon(Icons.delete, color: palette.danger),
              ),
              onDismissed: (direction) async {
                await ref.read(financeViewModelProvider.notifier).deleteTransaction(tx.id);
                if (!context.mounted) return;
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text(context.l10n.financeDeletedEntry(tx.description ?? financeCategoryLabel(context.l10n, tx.category)))),
                );
              },
              child: Container(
                margin: const EdgeInsets.only(bottom: 12),
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: palette.surfaceVariant,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: palette.border),
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
                          Text(tx.description ?? financeCategoryLabel(context.l10n, tx.category), style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                          const SizedBox(height: 2),
                          Text(
                            '${financeCategoryLabel(context.l10n, tx.category)} • ${DateFormat('yyyy-MM-dd').format(tx.date)}',
                            style: TextStyle(color: palette.textSecondary, fontSize: 11),
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
    );
  }
}
