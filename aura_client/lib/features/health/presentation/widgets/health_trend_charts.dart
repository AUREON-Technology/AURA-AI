import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/theme/theme.dart';

class HealthTrendCharts extends StatelessWidget {
  final List<HealthRecord> records;
  const HealthTrendCharts({super.key, required this.records});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Weekly Health Trends', style: Theme.of(context).textTheme.titleMedium),
            const Divider(color: Color(0x1F94A3B8), height: 20),
            Expanded(
              child: records.isEmpty
                  ? Center(
                      child: Text(
                        'Log metrics to populate weekly graphs',
                        style: TextStyle(color: AuraTheme.textSecondary.withValues(alpha: 0.5)),
                      ),
                    )
                  : Column(
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              const Text('Water Intake (ml)', style: TextStyle(fontSize: 12, color: AuraTheme.textSecondary)),
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
                              const Text('Weight Trends (kg)', style: TextStyle(fontSize: 12, color: AuraTheme.textSecondary)),
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
    final barGroups = <BarChartGroupData>[];
    var maxWater = 2000.0;
    for (var i = 0; i < records.length; i++) {
      final r = records[i];
      if (r.waterIntakeMl > maxWater) maxWater = r.waterIntakeMl.toDouble();
      barGroups.add(
        BarChartGroupData(
          x: i,
          barRods: [
            BarChartRodData(toY: r.waterIntakeMl.toDouble(), color: Colors.cyan, width: 14, borderRadius: BorderRadius.circular(4)),
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
                  return Text(DateFormat('E').format(records[idx].recordDate), style: const TextStyle(fontSize: 10, color: AuraTheme.textSecondary));
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
      return const Center(
        child: Text('Log weight to display trend line', style: TextStyle(fontSize: 11, color: AuraTheme.textSecondary)),
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
                  return Text(DateFormat('MM/dd').format(records[idx].recordDate), style: const TextStyle(fontSize: 9, color: AuraTheme.textSecondary));
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
            color: AuraTheme.accentIndigo,
            barWidth: 3,
            dotData: const FlDotData(show: true),
            belowBarData: BarAreaData(show: true, color: AuraTheme.accentIndigo.withValues(alpha: 0.15)),
          ),
        ],
      ),
    );
  }
}
