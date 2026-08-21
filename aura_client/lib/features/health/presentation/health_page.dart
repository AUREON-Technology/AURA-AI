import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/theme/theme.dart';
import '../data/health_repository.dart';
import 'health_view_model.dart';
import 'widgets/health_daily_dashboard.dart';
import 'widgets/health_trend_charts.dart';

class HealthPage extends ConsumerWidget {
  const HealthPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final selectedDate = ref.watch(healthViewModelProvider);
    final dailyRecordAsync = ref.watch(dailyHealthStreamProvider(selectedDate));
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
                Text('Health Monitor', style: Theme.of(context).textTheme.displayLarge?.copyWith(fontSize: 28)),
                const SizedBox(height: 4),
                Text(
                  'Track fitness parameters, log water, sleep, and review logs',
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
                const SizedBox(height: 24),
                Expanded(
                  child: LayoutBuilder(
                    builder: (context, constraints) {
                      final isDesktop = constraints.maxWidth > 900;
                      final dailySummary = dailyRecordAsync.when(
                        loading: () => const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo)),
                        error: (err, stack) => Text('Error: $err', style: const TextStyle(color: Colors.redAccent)),
                        data: (record) => HealthDailyDashboard(record: record),
                      );
                      final chartsWidget = weeklyRecordsAsync.when(
                        loading: () => const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo)),
                        error: (err, stack) => Text('Error: $err', style: const TextStyle(color: Colors.redAccent)),
                        data: (records) => HealthTrendCharts(records: records),
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
                      }
                      return ListView(
                        children: [
                          dailySummary,
                          const SizedBox(height: 24),
                          SizedBox(height: 480, child: chartsWidget),
                        ],
                      );
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
}
