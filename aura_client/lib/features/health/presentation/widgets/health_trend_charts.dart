import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

class HealthTrendCharts extends StatelessWidget {
  final List<HealthRecord> records;
  const HealthTrendCharts({super.key, required this.records});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(context.l10n.healthWeeklyTrendsTitle, style: Theme.of(context).textTheme.titleMedium),
            Divider(color: palette.border, height: 20),
            Expanded(
              child: records.isEmpty
                  ? Center(
                      child: Text(
                        context.l10n.healthWeeklyTrendsEmpty,
                        style: TextStyle(color: palette.textSecondary),
                      ),
                    )
                  : Column(
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              Text(context.l10n.healthWaterChartTitle, style: TextStyle(fontSize: 12, color: palette.textSecondary)),
                              const SizedBox(height: 8),
                              Expanded(child: _WaterBarChart(records: records)),
                            ],
                          ),
                        ),
                        const SizedBox(height: 24),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              Text(context.l10n.healthWeightChartTitle, style: TextStyle(fontSize: 12, color: palette.textSecondary)),
                              const SizedBox(height: 8),
                              Expanded(child: _WeightLineChart(records: records)),
                            ],
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

class _WaterBarChart extends StatelessWidget {
  final List<HealthRecord> records;
  const _WaterBarChart({required this.records});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final barGroups = <BarChartGroupData>[];
    var maxWater = 2000.0;
    for (var i = 0; i < records.length; i++) {
      final r = records[i];
      if (r.waterIntakeMl > maxWater) maxWater = r.waterIntakeMl.toDouble();
      barGroups.add(
        BarChartGroupData(
          x: i,
          barRods: [
            BarChartRodData(toY: r.waterIntakeMl.toDouble(), color: palette.info, width: 14, borderRadius: BorderRadius.circular(4)),
          ],
        ),
      );
    }

    return BarChart(
      BarChartData(
        maxY: maxWater * 1.1,
        gridData: const FlGridData(show: false),
        borderData: FlBorderData(show: false),
        titlesData: FlTitlesData(
          show: true,
          topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          leftTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              getTitlesWidget: (value, meta) {
                final idx = value.toInt();
                if (idx >= 0 && idx < records.length) {
                  return Text(DateFormat.E().format(records[idx].recordDate), style: TextStyle(fontSize: 10, color: palette.textSecondary));
                }
                return const Text('');
              },
            ),
          ),
        ),
        barGroups: barGroups,
      ),
    );
  }
}

class _WeightLineChart extends StatelessWidget {
  final List<HealthRecord> records;
  const _WeightLineChart({required this.records});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final spots = <FlSpot>[];
    var minWeight = 1000.0;
    var maxWeight = 0.0;
    var spotIdx = 0;
    for (final r in records) {
      if (r.weight != null) {
        final w = r.weight!;
        if (w < minWeight) minWeight = w;
        if (w > maxWeight) maxWeight = w;
        spots.add(FlSpot(spotIdx.toDouble(), w));
      }
      spotIdx++;
    }

    if (spots.isEmpty) {
      return Center(
        child: Text(context.l10n.healthWeightChartEmpty, style: TextStyle(fontSize: 11, color: palette.textSecondary)),
      );
    }

    final displayMin = minWeight == maxWeight ? minWeight - 5 : minWeight - 2;
    final displayMax = minWeight == maxWeight ? maxWeight + 5 : maxWeight + 2;

    return LineChart(
      LineChartData(
        minY: displayMin.clamp(0, double.infinity),
        maxY: displayMax,
        gridData: const FlGridData(show: false),
        borderData: FlBorderData(show: false),
        titlesData: FlTitlesData(
          show: true,
          topTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          leftTitles: const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              getTitlesWidget: (value, meta) {
                final idx = value.toInt();
                if (idx >= 0 && idx < records.length) {
                  return Text(DateFormat.Md().format(records[idx].recordDate), style: TextStyle(fontSize: 9, color: palette.textSecondary));
                }
                return const Text('');
              },
            ),
          ),
        ),
        lineBarsData: [
          LineChartBarData(
            spots: spots,
            isCurved: true,
            color: palette.brand,
            barWidth: 3,
            dotData: const FlDotData(show: true),
            belowBarData: BarAreaData(show: true, color: palette.brand.withValues(alpha: 0.15)),
          ),
        ],
      ),
    );
  }
}
