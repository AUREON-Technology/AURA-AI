import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import 'package:fl_chart/fl_chart.dart';
import '../data/health_repository.dart';
import '../../../core/theme/theme.dart';
import '../../../core/database/database.dart';

class HealthPage extends ConsumerStatefulWidget {
  const HealthPage({super.key});

  @override
  ConsumerState<HealthPage> createState() => _HealthPageState();
}

class _HealthPageState extends ConsumerState<HealthPage> {
  final DateTime _selectedDate = DateTime.now();

  // Target Goals
  final int _waterGoalMl = 2000;
  final int _sleepGoalMins = 480; // 8 hours
  final int _exerciseGoalMins = 30;
  final int _calorieGoalKcal = 2000;

  void _showLogDialog({
    required String title,
    required String labelText,
    required String hintText,
    required TextInputType keyboardType,
    required Function(String) onSubmit,
  }) {
    final controller = TextEditingController();
    showDialog(
      context: context,
      builder: (context) {
        return AlertDialog(
          backgroundColor: const Color(0xFF0F0C1B),
          surfaceTintColor: Colors.transparent,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(20),
            side: const BorderSide(color: Color(0x1F94A3B8)),
          ),
          title: Text(title, style: const TextStyle(color: Colors.white)),
          content: TextField(
            controller: controller,
            keyboardType: keyboardType,
            decoration: InputDecoration(
              labelText: labelText,
              hintText: hintText,
            ),
            style: const TextStyle(color: Colors.white),
            autofocus: true,
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text("Cancel", style: TextStyle(color: AuraTheme.textSecondary)),
            ),
            ElevatedButton(
              onPressed: () {
                final input = controller.text.trim();
                if (input.isNotEmpty) {
                  onSubmit(input);
                }
                Navigator.pop(context);
              },
              child: const Text("Save"),
            ),
          ],
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final dailyRecordAsync = ref.watch(dailyHealthStreamProvider(_selectedDate));
    final weeklyRecordsAsync = ref.watch(weeklyHealthStreamProvider);

    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment(-0.6, 0.6),
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
                Expanded(
                  child: LayoutBuilder(
                    builder: (context, constraints) {
                      final isDesktop = constraints.maxWidth > 900;

                      final dailySummary = dailyRecordAsync.when(
                        loading: () => const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo)),
                        error: (err, stack) => Text("Error: $err", style: const TextStyle(color: Colors.redAccent)),
                        data: (record) => _buildDailySummaryDashboard(record),
                      );

                      final chartsWidget = weeklyRecordsAsync.when(
                        loading: () => const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo)),
                        error: (err, stack) => Text("Error: $err", style: const TextStyle(color: Colors.redAccent)),
                        data: (records) => _buildTelemetryTrendCharts(records),
                      );

                      if (isDesktop) {
                        return Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Expanded(flex: 3, child: dailySummary),
                            const SizedBox(width: 24),
                            Expanded(flex: 2, child: chartsWidget),
                          ],
                        );
                      } else {
                        return ListView(
                          children: [
                            dailySummary,
                            const SizedBox(height: 24),
                            SizedBox(height: 480, child: chartsWidget),
                          ],
                        );
                      }
                    },
                  ),
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
          "Health Monitor",
          style: Theme.of(context).textTheme.displayLarge?.copyWith(fontSize: 28),
        ),
        const SizedBox(height: 4),
        Text(
          "Track fitness parameters, log water, sleep, and review logs",
          style: Theme.of(context).textTheme.bodyMedium,
        ),
      ],
    );
  }

  Widget _buildDailySummaryDashboard(HealthRecord? record) {
    final water = record?.waterIntakeMl ?? 0;
    final sleep = record?.sleepMinutes ?? 0;
    final exercise = record?.exerciseMinutes ?? 0;
    final calories = record?.caloriesConsumed ?? 0;
    final weight = record?.weight;

    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Row of stats summary cards (Water, Sleep, Exercise, Calories)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text(
                        "Daily Goals Progress",
                        style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white),
                      ),
                      if (weight != null)
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                          decoration: BoxDecoration(
                            color: AuraTheme.accentIndigo.withValues(alpha: 0.15),
                            borderRadius: BorderRadius.circular(20),
                            border: Border.all(color: AuraTheme.accentIndigo.withValues(alpha: 0.3)),
                          ),
                          child: Text(
                            "Weight: ${weight.toStringAsFixed(1)} kg",
                            style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: Colors.white),
                          ),
                        ),
                    ],
                  ),
                  const SizedBox(height: 20),
                  _buildProgressBar(
                    title: "Water Intake",
                    current: water,
                    target: _waterGoalMl,
                    unit: "ml",
                    color: Colors.cyan,
                    icon: Icons.local_drink_outlined,
                  ),
                  const SizedBox(height: 16),
                  _buildProgressBar(
                    title: "Sleep Duration",
                    current: sleep,
                    target: _sleepGoalMins,
                    unit: "mins",
                    color: AuraTheme.accentIndigo,
                    icon: Icons.nightlight_round_outlined,
                    customSubtitle: "${(sleep / 60).toStringAsFixed(1)} / 8.0 hrs",
                  ),
                  const SizedBox(height: 16),
                  _buildProgressBar(
                    title: "Exercise Duration",
                    current: exercise,
                    target: _exerciseGoalMins,
                    unit: "mins",
                    color: AuraTheme.accentAmber,
                    icon: Icons.fitness_center_outlined,
                  ),
                  const SizedBox(height: 16),
                  _buildProgressBar(
                    title: "Calories Consumed",
                    current: calories,
                    target: _calorieGoalKcal,
                    unit: "kcal",
                    color: Colors.pinkAccent,
                    icon: Icons.restaurant_outlined,
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 24),
          // Interactive Action Buttons
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    "Quick Logger Panel",
                    style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white),
                  ),
                  const SizedBox(height: 16),
                  Wrap(
                    spacing: 12,
                    runSpacing: 12,
                    children: [
                      ElevatedButton.icon(
                        onPressed: () async {
                          await ref.read(healthRepositoryProvider).logWater(_selectedDate, 250);
                        },
                        icon: const Icon(Icons.add, size: 16),
                        label: const Text("+250ml Water"),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: Colors.cyan,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () async {
                          await ref.read(healthRepositoryProvider).logWater(_selectedDate, 500);
                        },
                        icon: const Icon(Icons.add, size: 16),
                        label: const Text("+500ml Water"),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: Colors.cyan.shade700,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () {
                          _showLogDialog(
                            title: "Log Sleep Duration",
                            labelText: "Sleep Minutes",
                            hintText: "e.g. 480 for 8 hours",
                            keyboardType: TextInputType.number,
                            onSubmit: (input) async {
                              final mins = int.tryParse(input);
                              if (mins != null && mins > 0) {
                                await ref.read(healthRepositoryProvider).logSleep(_selectedDate, mins);
                              }
                            },
                          );
                        },
                        icon: const Icon(Icons.nightlight_outlined, size: 16),
                        label: const Text("Log Sleep"),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: AuraTheme.accentIndigo,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () {
                          _showLogDialog(
                            title: "Log Exercise Duration",
                            labelText: "Exercise Minutes",
                            hintText: "e.g. 30",
                            keyboardType: TextInputType.number,
                            onSubmit: (input) async {
                              final mins = int.tryParse(input);
                              if (mins != null && mins > 0) {
                                await ref.read(healthRepositoryProvider).logExercise(_selectedDate, mins);
                              }
                            },
                          );
                        },
                        icon: const Icon(Icons.fitness_center, size: 16),
                        label: const Text("Log Exercise"),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: AuraTheme.accentAmber,
                          foregroundColor: Colors.black87,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () {
                          _showLogDialog(
                            title: "Log Calories",
                            labelText: "Kcal Consumed",
                            hintText: "e.g. 500",
                            keyboardType: TextInputType.number,
                            onSubmit: (input) async {
                              final kcal = int.tryParse(input);
                              if (kcal != null && kcal > 0) {
                                await ref.read(healthRepositoryProvider).logCalories(_selectedDate, kcal);
                              }
                            },
                          );
                        },
                        icon: const Icon(Icons.restaurant, size: 16),
                        label: const Text("Log Calories"),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: Colors.pinkAccent,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () {
                          _showLogDialog(
                            title: "Log Current Weight",
                            labelText: "Weight (kg)",
                            hintText: "e.g. 74.5",
                            keyboardType: const TextInputType.numberWithOptions(decimal: true),
                            onSubmit: (input) async {
                              final w = double.tryParse(input);
                              if (w != null && w > 0) {
                                await ref.read(healthRepositoryProvider).logWeight(_selectedDate, w);
                              }
                            },
                          );
                        },
                        icon: const Icon(Icons.monitor_weight_outlined, size: 16),
                        label: const Text("Log Weight"),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: AuraTheme.accentTeal,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildProgressBar({
    required String title,
    required int current,
    required int target,
    required String unit,
    required Color color,
    required IconData icon,
    String? customSubtitle,
  }) {
    final pct = (current / target).clamp(0.0, 1.0);
    final pctText = (pct * 100).toStringAsFixed(0);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(icon, color: color, size: 18),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                title,
                style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
              ),
            ),
            Text(
              customSubtitle ?? "$current / $target $unit",
              style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 12),
            ),
            const SizedBox(width: 8),
            Text(
              "$pctText%",
              style: TextStyle(fontWeight: FontWeight.bold, color: color, fontSize: 13),
            ),
          ],
        ),
        const SizedBox(height: 6),
        ClipRRect(
          borderRadius: BorderRadius.circular(6),
          child: LinearProgressIndicator(
            value: pct,
            backgroundColor: color.withValues(alpha: 0.1),
            valueColor: AlwaysStoppedAnimation<Color>(color),
            minHeight: 10,
          ),
        ),
      ],
    );
  }

  Widget _buildTelemetryTrendCharts(List<HealthRecord> records) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              "Weekly Health Trends",
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const Divider(color: Color(0x1F94A3B8), height: 20),
            Expanded(
              child: records.isEmpty
                  ? Center(
                      child: Text(
                        "Log metrics to populate weekly graphs",
                        style: TextStyle(color: AuraTheme.textSecondary.withValues(alpha: 0.5)),
                      ),
                    )
                  : Column(
                      children: [
                        // Water Intake Bar Chart
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              const Text("Water Intake (ml)", style: TextStyle(fontSize: 12, color: AuraTheme.textSecondary)),
                              const SizedBox(height: 8),
                              Expanded(child: _buildWaterBarChart(records)),
                            ],
                          ),
                        ),
                        const SizedBox(height: 24),
                        // Weight Line Chart
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              const Text("Weight Trends (kg)", style: TextStyle(fontSize: 12, color: AuraTheme.textSecondary)),
                              const SizedBox(height: 8),
                              Expanded(child: _buildWeightLineChart(records)),
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

  Widget _buildWaterBarChart(List<HealthRecord> records) {
    final barGroups = <BarChartGroupData>[];
    double maxWater = 2000;

    for (int i = 0; i < records.length; i++) {
      final r = records[i];
      if (r.waterIntakeMl > maxWater) {
        maxWater = r.waterIntakeMl.toDouble();
      }
      barGroups.add(
        BarChartGroupData(
          x: i,
          barRods: [
            BarChartRodData(
              toY: r.waterIntakeMl.toDouble(),
              color: Colors.cyan,
              width: 14,
              borderRadius: BorderRadius.circular(4),
            ),
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
                  final date = records[idx].recordDate;
                  return Text(
                    DateFormat('E').format(date),
                    style: const TextStyle(fontSize: 10, color: AuraTheme.textSecondary),
                  );
                }
                return const Text("");
              },
            ),
          ),
        ),
        barGroups: barGroups,
      ),
    );
  }

  Widget _buildWeightLineChart(List<HealthRecord> records) {
    final spots = <FlSpot>[];
    double minWeight = 1000;
    double maxWeight = 0;

    int spotIdx = 0;
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
        child: Text("Log weight to display trend line", style: TextStyle(fontSize: 11, color: AuraTheme.textSecondary)),
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
                  final date = records[idx].recordDate;
                  return Text(
                    DateFormat('MM/dd').format(date),
                    style: const TextStyle(fontSize: 9, color: AuraTheme.textSecondary),
                  );
                }
                return const Text("");
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
            belowBarData: BarAreaData(
              show: true,
              color: AuraTheme.accentIndigo.withValues(alpha: 0.15),
            ),
          ),
        ],
      ),
    );
  }
}
