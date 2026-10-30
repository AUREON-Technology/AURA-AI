import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:fl_chart/fl_chart.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/theme/theme.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';
import 'package:aura_client/l10n/l10n.dart';

final telemetryLogsFutureProvider = FutureProvider.autoDispose<List<TelemetryLog>>((ref) async {
  final db = ref.watch(databaseProvider);
  return db.getTelemetry();
});

class AnalyticsPage extends ConsumerWidget {
  const AnalyticsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final logsAsync = ref.watch(telemetryLogsFutureProvider);
    final isMobile = MediaQuery.of(context).size.width < 768;

    return Scaffold(
      backgroundColor: AuraTheme.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: const Text(
          "System Analytics Dashboard",
          style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white),
        ),
        actions: [
          IconButton(
            tooltip: context.l10n.refresh,
            icon: const Icon(Icons.refresh, color: Colors.white),
            onPressed: () => ref.refresh(telemetryLogsFutureProvider),
          ),
        ],
      ),
      body: logsAsync.when(
        data: (logs) {
          if (logs.isEmpty) {
            return _buildEmptyState();
          }

          // Compute telemetry details
          final totalRequests = logs.where((l) => l.eventType == 'request').length;
          final failovers = logs.where((l) => l.eventType == 'failover').length;
          final errors = logs.where((l) => l.eventType == 'error').length;
          final averageLatency = totalRequests > 0
              ? logs.where((l) => l.eventType == 'request').map((l) => l.latencyMs).reduce((a, b) => a + b) / totalRequests
              : 0.0;

          // Inference path distribution (server-sse vs failover), not Gemini/offline models.
          int serverSseCount = logs.where((l) =>
              (l.modelUsed.contains('server-sse') || l.modelUsed == 'server') &&
              l.eventType == 'request' &&
              !l.isOffline).length;
          int failoverCount = logs.where((l) => l.eventType == 'failover' || l.isOffline).length;

          return Padding(
            padding: const EdgeInsets.all(24.0),
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // KPI Indicator cards row
                  _buildKPISection(isMobile, totalRequests, failovers, averageLatency, errors),
                  const SizedBox(height: 28),
                  
                  // Charts Grid
                  isMobile
                      ? Column(
                          children: [
                            _buildLatencyChartCard(logs),
                            const SizedBox(height: 20),
                            _buildModelDistributionCard(serverSseCount, failoverCount),
                            const SizedBox(height: 20),
                            _buildFailoverChartCard(totalRequests, failovers),
                          ],
                        )
                      : GridView.count(
                          shrinkWrap: true,
                          physics: const NeverScrollableScrollPhysics(),
                          crossAxisCount: 2,
                          crossAxisSpacing: 20,
                          mainAxisSpacing: 20,
                          childAspectRatio: 1.4,
                          children: [
                            _buildLatencyChartCard(logs),
                            _buildModelDistributionCard(serverSseCount, failoverCount),
                            _buildFailoverChartCard(totalRequests, failovers),
                            _buildTelemetriesHistoryCard(logs),
                          ],
                        ),
                ],
              ),
            ),
          );
        },
        loading: () => const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo)),
        error: (err, _) => Center(
          child: SingleChildScrollView(
            child: AuraFailureView(failure: AuraFailure.from(err)),
          ),
        ),
      ),
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.bar_chart, size: 72, color: Colors.white.withValues(alpha: 0.3)),
          const SizedBox(height: 16),
          const Text("No telemetry metrics collected yet", style: TextStyle(color: AuraTheme.textSecondary, fontSize: 16)),
          const SizedBox(height: 8),
          const Text("Metrics will appear once you stream prompts.", style: TextStyle(color: Colors.white30, fontSize: 13)),
        ],
      ),
    );
  }

  Widget _buildKPISection(bool isMobile, int total, int failovers, double latency, int errors) {
    final cards = [
      _buildKPICard("Total Requests", total.toString(), Icons.forum, AuraTheme.accentIndigo),
      _buildKPICard("Avg Latency", "${latency.toStringAsFixed(0)} ms", Icons.speed, AuraTheme.accentAmber),
      _buildKPICard("Failovers", failovers.toString(), Icons.loop, AuraTheme.accentTeal),
      _buildKPICard("Errors Logged", errors.toString(), Icons.error_outline, Colors.redAccent),
    ];

    if (isMobile) {
      return GridView.count(
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        crossAxisCount: 2,
        crossAxisSpacing: 12,
        mainAxisSpacing: 12,
        childAspectRatio: 1.6,
        children: cards,
      );
    }

    return Row(
      children: cards.map((c) => Expanded(child: Padding(padding: const EdgeInsets.symmetric(horizontal: 6), child: c))).toList(),
    );
  }

  Widget _buildKPICard(String title, String value, IconData icon, Color color) {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Row(
          children: [
            CircleAvatar(
              backgroundColor: color.withValues(alpha: 0.1),
              radius: 20,
              child: Icon(icon, color: color, size: 20),
            ),
            const SizedBox(width: 14),
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(title, style: const TextStyle(fontSize: 12, color: AuraTheme.textSecondary)),
                const SizedBox(height: 4),
                Text(value, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white)),
              ],
            )
          ],
        ),
      ),
    );
  }

  Widget _buildLatencyChartCard(List<TelemetryLog> logs) {
    final spots = <FlSpot>[];
    final reqs = logs.where((l) => l.eventType == 'request').toList();
    
    // Grab the last 10 points
    final count = reqs.length > 10 ? reqs.length - 10 : 0;
    int index = 0;
    for (int i = count; i < reqs.length; i++) {
      spots.add(FlSpot(index.toDouble(), reqs[i].latencyMs));
      index++;
    }

    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text("Latency History (Last 10 Request Streams)", style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white, fontSize: 14)),
            const SizedBox(height: 24),
            Expanded(
              child: spots.isEmpty
                  ? const Center(child: Text("Insufficient data", style: TextStyle(color: Colors.white24)))
                  : LineChart(
                      LineChartData(
                        gridData: const FlGridData(show: false),
                        titlesData: const FlTitlesData(show: false),
                        borderData: FlBorderData(show: false),
                        lineBarsData: [
                          LineChartBarData(
                            spots: spots,
                            isCurved: true,
                            color: AuraTheme.accentAmber,
                            barWidth: 3,
                            dotData: const FlDotData(show: true),
                            belowBarData: BarAreaData(
                              show: true,
                              color: AuraTheme.accentAmber.withValues(alpha: 0.1),
                            ),
                          ),
                        ],
                      ),
                    ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildModelDistributionCard(int serverSseCount, int failoverCount) {
    final sections = <PieChartSectionData>[
      if (serverSseCount > 0)
        PieChartSectionData(
          color: AuraTheme.accentIndigo,
          value: serverSseCount.toDouble(),
          title: 'server-sse ($serverSseCount)',
          radius: 50,
          titleStyle: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.white),
        ),
      if (failoverCount > 0)
        PieChartSectionData(
          color: AuraTheme.accentTeal,
          value: failoverCount.toDouble(),
          title: 'failover ($failoverCount)',
          radius: 50,
          titleStyle: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.white),
        ),
    ];

    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text("Inference Path Distribution", style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white, fontSize: 14)),
            const SizedBox(height: 24),
            Expanded(
              child: sections.isEmpty
                  ? const Center(child: Text("No inference data", style: TextStyle(color: Colors.white24)))
                  : PieChart(PieChartData(sections: sections)),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildFailoverChartCard(int total, int failovers) {
    final success = total - failovers;
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text("API Failover Statistics", style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white, fontSize: 14)),
            const SizedBox(height: 24),
            Expanded(
              child: total == 0
                  ? const Center(child: Text("No failover events", style: TextStyle(color: Colors.white24)))
                  : PieChart(
                      PieChartData(
                        sections: [
                          PieChartSectionData(
                            color: AuraTheme.accentTeal,
                            value: success.toDouble(),
                            title: 'Success ($success)',
                            radius: 50,
                            titleStyle: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.white),
                          ),
                          PieChartSectionData(
                            color: AuraTheme.accentAmber,
                            value: failovers.toDouble(),
                            title: 'Failovers ($failovers)',
                            radius: 50,
                            titleStyle: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.white),
                          ),
                        ],
                      ),
                    ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildTelemetriesHistoryCard(List<TelemetryLog> logs) {
    // Reverse logs to show latest first
    final latest = logs.reversed.take(5).toList();

    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text("Latest Telemetry Logs", style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white, fontSize: 14)),
            const SizedBox(height: 16),
            Expanded(
              child: ListView.builder(
                itemCount: latest.length,
                itemBuilder: (context, index) {
                  final log = latest[index];
                  final isErr = log.eventType == 'error';
                  final color = isErr ? Colors.redAccent : AuraTheme.textSecondary;

                  return Padding(
                    padding: const EdgeInsets.only(bottom: 8.0),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text("${log.eventType.toUpperCase()} (${log.modelUsed})", style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.bold)),
                        Text("${log.latencyMs.toStringAsFixed(0)}ms", style: const TextStyle(color: Colors.white54, fontSize: 11)),
                      ],
                    ),
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
