import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/theme/theme.dart';
import 'package:aura_client/l10n/l10n.dart';
import '../../data/finance_repository.dart';

class FinanceSummaryCards extends StatelessWidget {
  final FinanceStats stats;
  const FinanceSummaryCards({super.key, required this.stats});

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final isNarrow = constraints.maxWidth < 600;
        final balanceCard = _SummaryCard(
          title: context.l10n.financeBalance,
          amount: stats.balance,
          color: stats.balance >= 0 ? AuraTheme.accentTeal : Colors.redAccent,
          icon: Icons.account_balance_wallet,
        );
        final incomeCard = _SummaryCard(
          title: context.l10n.financeIncome,
          amount: stats.totalIncome,
          color: AuraTheme.accentTeal,
          icon: Icons.arrow_upward,
        );
        final expenseCard = _SummaryCard(
          title: context.l10n.financeExpenses,
          amount: stats.totalExpense,
          color: Colors.redAccent,
          icon: Icons.arrow_downward,
        );

        if (isNarrow) {
          return Column(
            children: [
              balanceCard,
              const SizedBox(height: 12),
              incomeCard,
              const SizedBox(height: 12),
              expenseCard,
            ],
          );
        }
        return Row(
          children: [
            Expanded(child: balanceCard),
            const SizedBox(width: 16),
            Expanded(child: incomeCard),
            const SizedBox(width: 16),
            Expanded(child: expenseCard),
          ],
        );
      },
    );
  }
}

class _SummaryCard extends StatelessWidget {
  final String title;
  final double amount;
  final Color color;
  final IconData icon;

  const _SummaryCard({
    required this.title,
    required this.amount,
    required this.color,
    required this.icon,
  });

  @override
  Widget build(BuildContext context) {
    final formatCurrency = NumberFormat.simpleCurrency(decimalDigits: 2);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: color.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Icon(icon, color: color, size: 24),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
                  ),
                  const SizedBox(height: 4),
                  FittedBox(
                    fit: BoxFit.scaleDown,
                    child: Text(
                      formatCurrency.format(amount),
                      style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold, color: color),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
