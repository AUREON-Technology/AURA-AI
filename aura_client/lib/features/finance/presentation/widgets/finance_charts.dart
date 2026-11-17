import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import '../../data/finance_repository.dart';

/// Categorical slice fills. The first three follow the theme's brand/status roles; the rest
/// are fixed accents, used only as fills (labels sit on [AuraPalette.onBrand] or the surface).
List<Color> _chartColors(AuraPalette palette) => [
      palette.brand,
      palette.warning,
      palette.success,
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
    final palette = AuraPalette.of(context);
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
                        ? Center(child: Text('No expenses logged', style: TextStyle(color: palette.textSecondary)))
                        : Padding(padding: const EdgeInsets.all(16.0), child: _PieChart(stats: stats)),
                  )
                : Expanded(
                    child: stats.totalExpense == 0
                        ? Center(child: Text('No expenses logged', style: TextStyle(color: palette.textSecondary)))
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
                  Divider(color: palette.border, height: 1),
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
                VerticalDivider(color: palette.border, width: 32),
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
    final palette = AuraPalette.of(context);
    final chartColors = _chartColors(palette);
    final sections = <PieChartSectionData>[];
    var colorIndex = 0;
    stats.expenseByCategory.forEach((category, amount) {
      final percentage = (amount / stats.totalExpense) * 100;
      final color = chartColors[colorIndex % chartColors.length];
      colorIndex++;
      sections.add(
        PieChartSectionData(
          value: amount,
          title: '${percentage.toStringAsFixed(0)}%',
          color: color,
          radius: 40,
          titleStyle: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: palette.onBrand),
          badgeWidget: Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
            decoration: BoxDecoration(
              color: palette.surface,
              borderRadius: BorderRadius.circular(6),
              border: Border.all(color: color.withValues(alpha: 0.5)),
            ),
            child: Text(category, style: TextStyle(fontSize: 8, color: palette.textPrimary)),
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
    final palette = AuraPalette.of(context);
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
                if (value.toInt() == 0) return Text('Income', style: TextStyle(fontSize: 10, color: palette.textSecondary));
                if (value.toInt() == 1) return Text('Expense', style: TextStyle(fontSize: 10, color: palette.textSecondary));
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
              BarChartRodData(toY: stats.totalIncome, color: palette.success, width: 20, borderRadius: BorderRadius.circular(4)),
            ],
          ),
          BarChartGroupData(
            x: 1,
            barRods: [
              BarChartRodData(toY: stats.totalExpense, color: palette.danger, width: 20, borderRadius: BorderRadius.circular(4)),
            ],
          ),
        ],
      ),
    );
  }
}
