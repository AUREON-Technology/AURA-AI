import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:fl_chart/fl_chart.dart';
import '../data/finance_repository.dart';
import '../../../core/theme/theme.dart';
import '../../../core/database/database.dart';

class FinancePage extends ConsumerStatefulWidget {
  const FinancePage({super.key});

  @override
  ConsumerState<FinancePage> createState() => _FinancePageState();
}

class _FinancePageState extends ConsumerState<FinancePage> {
  final _amountController = TextEditingController();
  final _categoryController = TextEditingController(text: 'Food');
  final _descriptionController = TextEditingController();
  String _transactionType = 'expense'; // expense or income
  DateTime _selectedDate = DateTime.now();

  final List<String> _categories = [
    'Food',
    'Studies',
    'Freelance',
    'Housing',
    'Entertainment',
    'Utilities',
    'Transport',
    'Other'
  ];

  final List<Color> _chartColors = [
    AuraTheme.accentIndigo,
    AuraTheme.accentAmber,
    AuraTheme.accentTeal,
    Colors.purpleAccent,
    Colors.pinkAccent,
    Colors.cyanAccent,
    Colors.deepOrangeAccent,
    Colors.blueAccent,
  ];

  @override
  void dispose() {
    _amountController.dispose();
    _categoryController.dispose();
    _descriptionController.dispose();
    super.dispose();
  }

  void _submitTransaction() async {
    final amount = double.tryParse(_amountController.text.trim());
    if (amount == null || amount <= 0) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text("Please enter a valid positive amount."),
          backgroundColor: Colors.redAccent,
        ),
      );
      return;
    }

    final category = _categoryController.text.trim();
    if (category.isEmpty) return;

    await ref.read(financeRepositoryProvider).addTransaction(
          type: _transactionType,
          amount: amount,
          category: category,
          description: _descriptionController.text.trim().isEmpty ? null : _descriptionController.text.trim(),
          date: _selectedDate,
        );

    _amountController.clear();
    _descriptionController.clear();
    setState(() {
      _selectedDate = DateTime.now();
    });

    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text("Transaction logged successfully!"),
          backgroundColor: AuraTheme.accentTeal,
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
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
                _buildHeader(),
                const SizedBox(height: 24),
                statsAsync.when(
                  loading: () => const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo)),
                  error: (err, stack) => Text("Error loading stats: $err", style: const TextStyle(color: Colors.redAccent)),
                  data: (stats) {
                    return Expanded(
                      child: LayoutBuilder(
                        builder: (context, constraints) {
                          final isDesktop = constraints.maxWidth > 950;
                          
                          final summaryWidget = _buildSummaryCards(stats);
                          final chartSection = _buildTelemetryCharts(stats);
                          final formWidget = _buildTransactionForm();
                          final historyWidget = _buildTransactionHistory(transactionsAsync);

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
                          } else {
                            return ListView(
                              children: [
                                summaryWidget,
                                const SizedBox(height: 24),
                                SizedBox(height: 380, child: chartSection),
                                const SizedBox(height: 24),
                                formWidget,
                                const SizedBox(height: 24),
                                SizedBox(height: 400, child: historyWidget),
                              ],
                            );
                          }
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

  Widget _buildHeader() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          "Finance Ledger",
          style: Theme.of(context).textTheme.displayLarge?.copyWith(fontSize: 28),
        ),
        const SizedBox(height: 4),
        const Text(
          "Track incomes, expenses, and analyze financial distributions",
          style: TextStyle(color: AuraTheme.textSecondary, fontSize: 14),
        ),
      ],
    );
  }

  Widget _buildSummaryCards(FinanceStats stats) {
    return Row(
      children: [
        Expanded(
          child: _buildSummaryCard(
            title: "Balance",
            amount: stats.balance,
            color: stats.balance >= 0 ? AuraTheme.accentTeal : Colors.redAccent,
            icon: Icons.account_balance_wallet,
          ),
        ),
        const SizedBox(width: 16),
        Expanded(
          child: _buildSummaryCard(
            title: "Income",
            amount: stats.totalIncome,
            color: AuraTheme.accentTeal,
            icon: Icons.arrow_upward,
          ),
        ),
        const SizedBox(width: 16),
        Expanded(
          child: _buildSummaryCard(
            title: "Expenses",
            amount: stats.totalExpense,
            color: Colors.redAccent,
            icon: Icons.arrow_downward,
          ),
        ),
      ],
    );
  }

  Widget _buildSummaryCard({
    required String title,
    required double amount,
    required Color color,
    required IconData icon,
  }) {
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
                    style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
                  ),
                  const SizedBox(height: 4),
                  FittedBox(
                    fit: BoxFit.scaleDown,
                    child: Text(
                      formatCurrency.format(amount),
                      style: TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.bold,
                        color: color,
                      ),
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

  Widget _buildTelemetryCharts(FinanceStats stats) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Row(
          children: [
            // Expense Pie Chart
            Expanded(
              flex: 3,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    "Expenses by Category",
                    style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
                  ),
                  const SizedBox(height: 16),
                  Expanded(
                    child: stats.totalExpense == 0
                        ? Center(
                            child: Text(
                              "No expenses logged",
                              style: TextStyle(color: AuraTheme.textSecondary.withValues(alpha: 0.6)),
                            ),
                          )
                        : _buildPieChart(stats),
                  ),
                ],
              ),
            ),
            const VerticalDivider(color: Color(0x1F94A3B8), width: 32),
            // Comparison Bar Chart
            Expanded(
              flex: 2,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    "Income vs Expense",
                    style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
                  ),
                  const SizedBox(height: 16),
                  Expanded(
                    child: _buildBarChart(stats),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildPieChart(FinanceStats stats) {
    final sections = <PieChartSectionData>[];
    int colorIndex = 0;

    stats.expenseByCategory.forEach((category, amount) {
      final percentage = (amount / stats.totalExpense) * 100;
      final color = _chartColors[colorIndex % _chartColors.length];
      colorIndex++;

      sections.add(
        PieChartSectionData(
          value: amount,
          title: "${percentage.toStringAsFixed(0)}%",
          color: color,
          radius: 40,
          titleStyle: const TextStyle(
            fontSize: 11,
            fontWeight: FontWeight.bold,
            color: Colors.white,
          ),
          badgeWidget: _buildBadge(category, color),
          badgePositionPercentageOffset: 1.4,
        ),
      );
    });

    return PieChart(
      PieChartData(
        sections: sections,
        sectionsSpace: 2,
        centerSpaceRadius: 35,
      ),
    );
  }

  Widget _buildBadge(String category, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: const Color(0xFF0F0C1B),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: color.withValues(alpha: 0.5)),
      ),
      child: Text(
        category,
        style: const TextStyle(fontSize: 8, color: Colors.white),
      ),
    );
  }

  Widget _buildBarChart(FinanceStats stats) {
    final maxVal = stats.totalIncome > stats.totalExpense 
        ? stats.totalIncome 
        : stats.totalExpense;
    final maxY = maxVal == 0 ? 100.0 : maxVal * 1.2;

    return BarChart(
      BarChartData(
        maxY: maxY,
        barTouchData: BarTouchData(enabled: true),
        titlesData: FlTitlesData(
          show: true,
          topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          leftTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              getTitlesWidget: (value, meta) {
                if (value.toInt() == 0) return const Text("Income", style: TextStyle(fontSize: 10, color: AuraTheme.textSecondary));
                if (value.toInt() == 1) return const Text("Expense", style: TextStyle(fontSize: 10, color: AuraTheme.textSecondary));
                return const Text("");
              },
            ),
          ),
        ),
        gridData: const FlGridData(show: false),
        borderData: FlBorderData(show: false),
        barGroups: [
          BarChartGroupData(
            x: 0,
            barRods: [
              BarChartRodData(
                toY: stats.totalIncome,
                color: AuraTheme.accentTeal,
                width: 20,
                borderRadius: BorderRadius.circular(4),
              ),
            ],
          ),
          BarChartGroupData(
            x: 1,
            barRods: [
              BarChartRodData(
                toY: stats.totalExpense,
                color: Colors.redAccent,
                width: 20,
                borderRadius: BorderRadius.circular(4),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildTransactionForm() {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              "Log Transaction",
              style: TextStyle(fontWeight: FontWeight.bold, fontSize: 15),
            ),
            const SizedBox(height: 16),
            // Type Selector
            Row(
              children: [
                Expanded(
                  child: ChoiceChip(
                    label: const Center(child: Text("Expense")),
                    selected: _transactionType == 'expense',
                    onSelected: (val) {
                      if (val) setState(() => _transactionType = 'expense');
                    },
                    selectedColor: Colors.redAccent.withValues(alpha: 0.2),
                    checkmarkColor: Colors.redAccent,
                    labelStyle: TextStyle(
                      color: _transactionType == 'expense' ? Colors.redAccent : AuraTheme.textSecondary,
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: ChoiceChip(
                    label: const Center(child: Text("Income")),
                    selected: _transactionType == 'income',
                    onSelected: (val) {
                      if (val) setState(() => _transactionType = 'income');
                    },
                    selectedColor: AuraTheme.accentTeal.withValues(alpha: 0.2),
                    checkmarkColor: AuraTheme.accentTeal,
                    labelStyle: TextStyle(
                      color: _transactionType == 'income' ? AuraTheme.accentTeal : AuraTheme.textSecondary,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  flex: 3,
                  child: TextField(
                    controller: _amountController,
                    keyboardType: const TextInputType.numberWithOptions(decimal: true),
                    decoration: const InputDecoration(
                      labelText: r'Amount ($)',
                      hintText: '0.00',
                    ),
                    style: const TextStyle(color: Colors.white),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  flex: 2,
                  child: DropdownButtonFormField<String>(
                    initialValue: _categories.contains(_categoryController.text) ? _categoryController.text : 'Other',
                    dropdownColor: const Color(0xFF0F0C1B),
                    style: const TextStyle(color: Colors.white),
                    decoration: const InputDecoration(
                      labelText: 'Category',
                    ),
                    items: _categories.map((cat) {
                      return DropdownMenuItem(
                        value: cat,
                        child: Text(cat),
                      );
                    }).toList(),
                    onChanged: (val) {
                      if (val != null) {
                        setState(() {
                          _categoryController.text = val;
                        });
                      }
                    },
                  ),
                ),
              ],
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
            ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.calendar_month, color: AuraTheme.accentIndigo),
              title: const Text("Date", style: TextStyle(fontSize: 13, color: AuraTheme.textSecondary)),
              subtitle: Text(DateFormat('yyyy-MM-dd').format(_selectedDate),
                  style: const TextStyle(color: Colors.white, fontSize: 14)),
              trailing: IconButton(
                icon: const Icon(Icons.edit, color: AuraTheme.textSecondary),
                onPressed: () async {
                  final date = await showDatePicker(
                    context: context,
                    initialDate: _selectedDate,
                    firstDate: DateTime.now().subtract(const Duration(days: 365)),
                    lastDate: DateTime.now(),
                  );
                  if (date != null) {
                    setState(() {
                      _selectedDate = date;
                    });
                  }
                },
              ),
            ),
            const SizedBox(height: 16),
            ElevatedButton(
              onPressed: _submitTransaction,
              style: ElevatedButton.styleFrom(
                backgroundColor: _transactionType == 'expense' ? Colors.redAccent : AuraTheme.accentTeal,
              ),
              child: const Text("Submit Ledger Entry"),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildTransactionHistory(AsyncValue<List<FinanceRecord>> transactionsAsync) {
    final formatCurrency = NumberFormat.simpleCurrency(decimalDigits: 2);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              "Recent Activities",
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const Divider(color: Color(0x1F94A3B8), height: 20),
            Expanded(
              child: transactionsAsync.when(
                loading: () => const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo)),
                error: (err, stack) => Text("Error: $err", style: const TextStyle(color: Colors.redAccent)),
                data: (txs) {
                  if (txs.isEmpty) {
                    return Center(
                      child: Text(
                        "No activities recorded yet",
                        style: TextStyle(color: AuraTheme.textSecondary.withValues(alpha: 0.5)),
                      ),
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
                          await ref.read(financeRepositoryProvider).deleteTransaction(tx.id);
                          if (!context.mounted) return;
                          ScaffoldMessenger.of(context).showSnackBar(
                            SnackBar(
                              content: Text("Deleted entry: ${tx.description ?? tx.category}"),
                            ),
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
                                decoration: BoxDecoration(
                                  color: color.withValues(alpha: 0.1),
                                  shape: BoxShape.circle,
                                ),
                                child: Icon(
                                  isExpense ? Icons.arrow_downward : Icons.arrow_upward,
                                  color: color,
                                  size: 16,
                                ),
                              ),
                              const SizedBox(width: 12),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      tx.description ?? tx.category,
                                      style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
                                    ),
                                    const SizedBox(height: 2),
                                    Text(
                                      "${tx.category} • ${DateFormat('yyyy-MM-dd').format(tx.date)}",
                                      style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 11),
                                    ),
                                  ],
                                ),
                              ),
                              Text(
                                "${isExpense ? '-' : '+'}${formatCurrency.format(tx.amount)}",
                                style: TextStyle(
                                  fontWeight: FontWeight.bold,
                                  color: color,
                                  fontSize: 14,
                                ),
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
