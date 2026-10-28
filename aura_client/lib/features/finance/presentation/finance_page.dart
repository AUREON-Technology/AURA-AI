import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/theme/theme.dart';
import '../data/finance_repository.dart';
import 'widgets/finance_charts.dart';
import 'widgets/finance_summary_cards.dart';
import 'widgets/finance_transaction_form.dart';
import 'widgets/finance_transaction_history.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';

class FinancePage extends ConsumerWidget {
  const FinancePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final statsAsync = ref.watch(financeStatsProvider);
    final transactionsAsync = ref.watch(financeTransactionsStreamProvider);

    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment(-0.6, -0.6),
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
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('Finance Ledger', style: Theme.of(context).textTheme.displayLarge?.copyWith(fontSize: 28)),
                const SizedBox(height: 4),
                const Text(
                  'Track incomes, expenses, and analyze financial distributions',
                  style: TextStyle(color: AuraTheme.textSecondary, fontSize: 14),
                ),
                const SizedBox(height: 24),
                statsAsync.when(
                  loading: () => const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo)),
                  error: (err, stack) => AuraFailureView(
                    failure: AuraFailure.from(err),
                    compact: true,
                    onRetry: () => ref.invalidate(financeStatsProvider),
                  ),
                  data: (stats) {
                    return Expanded(
                      child: LayoutBuilder(
                        builder: (context, constraints) {
                          final isDesktop = constraints.maxWidth > 950;
                          final summaryWidget = FinanceSummaryCards(stats: stats);
                          final chartSection = FinanceCharts(stats: stats);
                          const formWidget = FinanceTransactionForm();
                          final historyWidget = FinanceTransactionHistory(transactionsAsync: transactionsAsync);

                          if (isDesktop) {
                            return Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Expanded(
                                  flex: 3,
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.stretch,
                                    children: [
                                      summaryWidget,
                                      const SizedBox(height: 24),
                                      Expanded(child: chartSection),
                                    ],
                                  ),
                                ),
                                const SizedBox(width: 24),
                                Expanded(
                                  flex: 2,
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.stretch,
                                    children: [
                                      formWidget,
                                      const SizedBox(height: 24),
                                      Expanded(child: historyWidget),
                                    ],
                                  ),
                                ),
                              ],
                            );
                          }
                          final isVerticalCharts = constraints.maxWidth < 600;
                          return ListView(
                            padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
                            children: [
                              summaryWidget,
                              const SizedBox(height: 24),
                              isVerticalCharts ? chartSection : SizedBox(height: 380, child: chartSection),
                              const SizedBox(height: 24),
                              formWidget,
                              const SizedBox(height: 24),
                              SizedBox(height: 400, child: historyWidget),
                            ],
                          );
                        },
                      ),
                    );
                  },
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
