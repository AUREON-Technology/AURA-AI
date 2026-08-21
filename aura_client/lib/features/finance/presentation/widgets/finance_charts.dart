import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import 'package:aura_client/core/theme/theme.dart';
import '../../data/finance_repository.dart';

final _chartColors = [
  AuraTheme.accentIndigo,
  AuraTheme.accentAmber,
  AuraTheme.accentTeal,
  Colors.purpleAccent,
  Colors.pinkAccent,
  Colors.cyanAccent,
  Colors.deepOrangeAccent,
  Colors.blueAccent,
];

class FinanceCharts extends StatelessWidget {
  final FinanceStats stats;
  const FinanceCharts({super.key, required this.stats});

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final isNarrow = constraints.maxWidth < 600;
        final pieChartSection = Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('Expenses by Category', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
            const SizedBox(height: 16),
            isNarrow
                ? SizedBox(
                    height: 200,
                    child: stats.totalExpense == 0
                        ? Center(child: Text('No expenses logged', style: TextStyle(color: AuraTheme.textSecondary.withValues(alpha: 0.6))))
                        : Padding(padding: const EdgeInsets.all(16.0), child: _PieChart(stats: stats)),
                  )
                : Expanded(
                    child: stats.totalExpense == 0
                        ? Center(child: Text('No expenses logged', style: TextStyle(color: AuraTheme.textSecondary.withValues(alpha: 0.6))))
                        : Padding(padding: const EdgeInsets.all(16.0), child: _PieChart(stats: stats)),
                  ),
          ],
        );
        final barChartSection = Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('Income vs Expense', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14)),
            const SizedBox(height: 16),
            isNarrow ? SizedBox(height: 180, child: _BarChart(stats: stats)) : Expanded(child: _BarChart(stats: stats)),
          ],
        );

        if (isNarrow) {
          return Card(
            child: Padding(
              padding: const EdgeInsets.all(20.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  pieChartSection,
                  const SizedBox(height: 24),
                  const Divider(color: Color(0x1F94A3B8), height: 1),
                  const SizedBox(height: 24),
                  barChartSection,
                ],
              ),
            ),
          );
        }
        return Card(
          child: Padding(
            padding: const EdgeInsets.all(20.0),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(flex: 3, child: pieChartSection),
                const VerticalDivider(color: Color(0x1F94A3B8), width: 32),
                Expanded(flex: 2, child: barChartSection),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _PieChart extends StatelessWidget {
  final FinanceStats stats;
  const _PieChart({required this.stats});

  @override
  Widget build(BuildContext context) {
    final sections = <PieChartSectionData>[];
    var colorIndex = 0;
    stats.expenseByCategory.forEach((category, amount) {
      final percentage = (amount / stats.totalExpense) * 100;
      final color = _chartColors[colorIndex % _chartColors.length];
      colorIndex++;
      sections.add(
        PieChartSectionData(
          value: amount,
          title: '${percentage.toStringAsFixed(0)}%',
          color: color,
          radius: 40,
          titleStyle: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.white),
          badgeWidget: Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
            decoration: BoxDecoration(
              color: const Color(0xFF0F0C1B),
              borderRadius: BorderRadius.circular(6),
              border: Border.all(color: color.withValues(alpha: 0.5)),
            ),
            child: Text(category, style: const TextStyle(fontSize: 8, color: Colors.white)),
          ),
          badgePositionPercentageOffset: 1.4,
        ),
      );
    });
    return PieChart(PieChartData(sections: sections, sectionsSpace: 2, centerSpaceRadius: 35));
  }
}

class _BarChart extends StatelessWidget {
  final FinanceStats stats;
  const _BarChart({required this.stats});

  @override
  Widget build(BuildContext context) {
    final maxVal = stats.totalIncome > stats.totalExpense ? stats.totalIncome : stats.totalExpense;
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
                if (value.toInt() == 0) return const Text('Income', style: TextStyle(fontSize: 10, color: AuraTheme.textSecondary));
                if (value.toInt() == 1) return const Text('Expense', style: TextStyle(fontSize: 10, color: AuraTheme.textSecondary));
                return const Text('');
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
              BarChartRodData(toY: stats.totalIncome, color: AuraTheme.accentTeal, width: 20, borderRadius: BorderRadius.circular(4)),
            ],
          ),
          BarChartGroupData(
            x: 1,
            barRods: [
              BarChartRodData(toY: stats.totalExpense, color: Colors.redAccent, width: 20, borderRadius: BorderRadius.circular(4)),
            ],
          ),
        ],
      ),
    );
  }
}
