import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import '../../../core/theme/theme.dart';
import '../../../core/ai/offline_agents/offline_agent_manager.dart';
import '../../../core/ai/offline_agents/inference_provider.dart';
import '../../tasks/data/task_repository.dart';
import '../../calendar/data/calendar_repository.dart';
import '../../finance/data/finance_repository.dart';
import '../../health/data/health_repository.dart';
import '../../analytics/presentation/analytics_page.dart';

class HomePage extends ConsumerWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tasksAsync = ref.watch(tasksStreamProvider);
    final calendarAsync = ref.watch(calendarEventsStreamProvider);
    final financeAsync = ref.watch(financeTransactionsStreamProvider);
    final healthAsync = ref.watch(dailyHealthStreamProvider(DateTime.now()));
    final ollamaHealthAsync = ref.watch(ollamaHealthProvider);

    final isMobile = MediaQuery.of(context).size.width < 768;

    return Scaffold(
      backgroundColor: AuraTheme.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Row(
          children: [
            Container(
              width: 32,
              height: 32,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(8),
                gradient: AuraTheme.primaryGradient,
              ),
              child: const Icon(Icons.blur_on, color: Colors.white, size: 20),
            ),
            const SizedBox(width: 12),
            const Text(
              "AURA AI Dashboard",
              style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white),
            ),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.settings, color: Colors.white70),
            onPressed: () => context.go('/settings'),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: SingleChildScrollView(
        child: Padding(
          padding: const EdgeInsets.all(24.0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _buildWelcomeSection(context),
              const SizedBox(height: 24),
              // Quick status widgets
              _buildQuickStatusRow(
                isMobile,
                tasksAsync,
                financeAsync,
                healthAsync,
                calendarAsync,
              ),
              const SizedBox(height: 24),
              // Dynamic details layout
              isMobile
                  ? Column(
                      children: [
                        _buildTasksOverviewCard(context, tasksAsync),
                        const SizedBox(height: 20),
                        _buildEventsOverviewCard(context, calendarAsync),
                        const SizedBox(height: 20),
                        _buildHealthOverviewCard(context, healthAsync),
                        const SizedBox(height: 20),
                        _buildAISystemHealthCard(ref, ollamaHealthAsync),
                      ],
                    )
                  : GridView.count(
                      shrinkWrap: true,
                      physics: const NeverScrollableScrollPhysics(),
                      crossAxisCount: 2,
                      crossAxisSpacing: 20,
                      mainAxisSpacing: 20,
                      childAspectRatio: 1.55,
                      children: [
                        _buildTasksOverviewCard(context, tasksAsync),
                        _buildEventsOverviewCard(context, calendarAsync),
                        _buildHealthOverviewCard(context, healthAsync),
                        _buildAISystemHealthCard(ref, ollamaHealthAsync),
                      ],
                    ),
              const SizedBox(height: 24),
              _buildQuickChatCard(context),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildWelcomeSection(BuildContext context) {
    final now = DateTime.now();
    final formatter = DateFormat('EEEE, MMMM d, yyyy');

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          "Welcome back,",
          style: TextStyle(color: AuraTheme.textSecondary, fontSize: 16),
        ),
        const SizedBox(height: 4),
        const Text(
          "AURA User",
          style: TextStyle(color: Colors.white, fontSize: 28, fontWeight: FontWeight.bold),
        ),
        const SizedBox(height: 6),
        Text(
          formatter.format(now),
          style: const TextStyle(color: AuraTheme.accentAmber, fontSize: 13, fontWeight: FontWeight.w500),
        ),
      ],
    );
  }

  Widget _buildQuickStatusRow(
    bool isMobile,
    AsyncValue<List<dynamic>> tasks,
    AsyncValue<List<dynamic>> finance,
    AsyncValue<dynamic> health,
    AsyncValue<List<dynamic>> calendar,
  ) {
    // Computations from states
    final activeTasksCount = tasks.value?.where((t) => !t.isCompleted).length ?? 0;
    
    double totalExpenses = 0.0;
    if (finance.value != null) {
      for (final record in finance.value!) {
        if (record.type == 'expense') {
          totalExpenses += record.amount;
        }
      }
    }

    final waterMl = health.value?.waterIntakeMl ?? 0;
    final nextEventTitle = calendar.value != null && calendar.value!.isNotEmpty
        ? calendar.value!.first.title
        : "None";

    final cards = [
      _buildQuickStatCard("Active Tasks", activeTasksCount.toString(), Icons.playlist_add_check, AuraTheme.accentIndigo),
      _buildQuickStatCard("Today's Water", "$waterMl mL", Icons.water_drop, Colors.blueAccent),
      _buildQuickStatCard("Expenses", "\$${totalExpenses.toStringAsFixed(0)}", Icons.monetization_on, AuraTheme.accentAmber),
      _buildQuickStatCard("Next Event", nextEventTitle, Icons.event_note, AuraTheme.accentTeal),
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
      children: cards.map((c) => Expanded(child: Padding(padding: const EdgeInsets.symmetric(horizontal: 4), child: c))).toList(),
    );
  }

  Widget _buildQuickStatCard(String title, String value, IconData icon, Color color) {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Row(
          children: [
            CircleAvatar(
              backgroundColor: color.withOpacity(0.1),
              radius: 18,
              child: Icon(icon, color: color, size: 18),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(title, style: const TextStyle(fontSize: 11, color: AuraTheme.textSecondary), overflow: TextOverflow.ellipsis),
                  const SizedBox(height: 4),
                  Text(value, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white), overflow: TextOverflow.ellipsis),
                ],
              ),
            )
          ],
        ),
      ),
    );
  }

  Widget _buildTasksOverviewCard(BuildContext context, AsyncValue<List<dynamic>> tasksAsync) {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text("Task Focus List", style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white, fontSize: 14)),
                TextButton(
                  onPressed: () => context.go('/tasks'),
                  child: const Text("View All", style: TextStyle(color: AuraTheme.accentIndigo, fontSize: 12)),
                )
              ],
            ),
            const SizedBox(height: 10),
            Expanded(
              child: tasksAsync.when(
                data: (tasks) {
                  final active = tasks.where((t) => !t.isCompleted).take(3).toList();
                  if (active.isEmpty) {
                    return const Center(child: Text("No active tasks!", style: TextStyle(color: Colors.white24, fontSize: 12)));
                  }
                  return ListView.builder(
                    itemCount: active.length,
                    itemBuilder: (context, index) {
                      final task = active[index];
                      return Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4.0),
                        child: Row(
                          children: [
                            const Icon(Icons.circle_outlined, size: 14, color: AuraTheme.accentIndigo),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                task.title,
                                style: const TextStyle(color: Colors.white, fontSize: 13),
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                          ],
                        ),
                      );
                    },
                  );
                },
                loading: () => const Center(child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))),
                error: (e, _) => const Text("Error loading tasks", style: TextStyle(color: Colors.redAccent, fontSize: 12)),
              ),
            )
          ],
        ),
      ),
    );
  }

  Widget _buildEventsOverviewCard(BuildContext context, AsyncValue<List<dynamic>> calendarAsync) {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text("Upcoming Events", style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white, fontSize: 14)),
                TextButton(
                  onPressed: () => context.go('/calendar'),
                  child: const Text("Calendar", style: TextStyle(color: AuraTheme.accentIndigo, fontSize: 12)),
                )
              ],
            ),
            const SizedBox(height: 10),
            Expanded(
              child: calendarAsync.when(
                data: (events) {
                  final upcoming = events.take(3).toList();
                  if (upcoming.isEmpty) {
                    return const Center(child: Text("No events scheduled", style: TextStyle(color: Colors.white24, fontSize: 12)));
                  }
                  return ListView.builder(
                    itemCount: upcoming.length,
                    itemBuilder: (context, index) {
                      final event = upcoming[index];
                      final timeStr = DateFormat('hh:mm a').format(event.startTime);
                      return Padding(
                        padding: const EdgeInsets.symmetric(vertical: 4.0),
                        child: Row(
                          children: [
                            const Icon(Icons.calendar_today_outlined, size: 12, color: AuraTheme.accentAmber),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                "${event.title} ($timeStr)",
                                style: const TextStyle(color: Colors.white, fontSize: 13),
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                          ],
                        ),
                      );
                    },
                  );
                },
                loading: () => const Center(child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))),
                error: (e, _) => const Text("Error loading calendar", style: TextStyle(color: Colors.redAccent, fontSize: 12)),
              ),
            )
          ],
        ),
      ),
    );
  }

  Widget _buildHealthOverviewCard(BuildContext context, AsyncValue<dynamic> healthAsync) {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text("Health & Fitness", style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white, fontSize: 14)),
                TextButton(
                  onPressed: () => context.go('/health'),
                  child: const Text("Track Health", style: TextStyle(color: AuraTheme.accentIndigo, fontSize: 12)),
                )
              ],
            ),
            const SizedBox(height: 10),
            Expanded(
              child: healthAsync.when(
                data: (record) {
                  if (record == null) {
                    return const Center(child: Text("No health records today", style: TextStyle(color: Colors.white24, fontSize: 12)));
                  }
                  return Column(
                    children: [
                      _buildHealthMetricRow("Water", "${record.waterIntakeMl} mL", Icons.water_drop, Colors.blueAccent),
                      const SizedBox(height: 6),
                      _buildHealthMetricRow("Sleep", "${(record.sleepMinutes / 60).toStringAsFixed(1)} hrs", Icons.bedtime, Colors.purpleAccent),
                      const SizedBox(height: 6),
                      _buildHealthMetricRow("Exercise", "${record.exerciseMinutes} mins", Icons.fitness_center, AuraTheme.accentTeal),
                    ],
                  );
                },
                loading: () => const Center(child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))),
                error: (e, _) => const Text("Error loading health statistics", style: TextStyle(color: Colors.redAccent, fontSize: 12)),
              ),
            )
          ],
        ),
      ),
    );
  }

  Widget _buildHealthMetricRow(String label, String val, IconData icon, Color color) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Row(
          children: [
            Icon(icon, size: 14, color: color),
            const SizedBox(width: 8),
            Text(label, style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 12)),
          ],
        ),
        Text(val, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 12)),
      ],
    );
  }

  Widget _buildAISystemHealthCard(WidgetRef ref, AsyncValue<bool> healthAsync) {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text("AI System Health", style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white, fontSize: 14)),
                TextButton(
                  onPressed: () {
                    ref.refresh(telemetryLogsFutureProvider);
                    ref.refresh(ollamaHealthProvider);
                  },
                  child: const Text("Telemetry", style: TextStyle(color: AuraTheme.accentIndigo, fontSize: 12)),
                )
              ],
            ),
            const SizedBox(height: 10),
            Expanded(
              child: healthAsync.when(
                data: (active) {
                  return Column(
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          const Text("Local Inference Provider", style: TextStyle(color: AuraTheme.textSecondary, fontSize: 12)),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                            decoration: BoxDecoration(
                              color: active ? AuraTheme.accentTeal.withOpacity(0.15) : Colors.redAccent.withOpacity(0.15),
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: Text(
                              active ? "Operational" : "Offline",
                              style: TextStyle(
                                color: active ? AuraTheme.accentTeal : Colors.redAccent,
                                fontWeight: FontWeight.bold,
                                fontSize: 10,
                              ),
                            ),
                          )
                        ],
                      ),
                      const SizedBox(height: 8),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          const Text("Failover Middleware", style: TextStyle(color: AuraTheme.textSecondary, fontSize: 12)),
                          const Text(
                            "Active (Auto)",
                            style: TextStyle(color: Colors.white70, fontWeight: FontWeight.bold, fontSize: 11),
                          )
                        ],
                      ),
                      const SizedBox(height: 8),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          const Text("Privacy Mode", style: TextStyle(color: AuraTheme.textSecondary, fontSize: 12)),
                          const Text(
                            "Encrypted Local SQLite",
                            style: TextStyle(color: AuraTheme.accentAmber, fontWeight: FontWeight.bold, fontSize: 11),
                          )
                        ],
                      ),
                    ],
                  );
                },
                loading: () => const Center(child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))),
                error: (err, _) => const Center(child: Text("Offline", style: TextStyle(color: Colors.redAccent, fontSize: 12))),
              ),
            )
          ],
        ),
      ),
    );
  }

  Widget _buildQuickChatCard(BuildContext context) {
    final textController = TextEditingController();

    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              "Quick AI Assistance",
              style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white, fontSize: 14),
            ),
            const SizedBox(height: 6),
            const Text(
              "Ask AURA anything, schedule tasks, log finance entries, or query memory facts.",
              style: TextStyle(color: AuraTheme.textSecondary, fontSize: 12),
            ),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: textController,
                    decoration: InputDecoration(
                      hintText: "Type your query...",
                      filled: true,
                      fillColor: Colors.black26,
                      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                    ),
                    style: const TextStyle(color: Colors.white, fontSize: 13),
                  ),
                ),
                const SizedBox(width: 12),
                ElevatedButton(
                  onPressed: () {
                    final query = textController.text.trim();
                    if (query.isNotEmpty) {
                      context.go('/chat', extra: query);
                    }
                  },
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AuraTheme.accentIndigo,
                    padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
                  ),
                  child: const Icon(Icons.send, size: 16),
                )
              ],
            )
          ],
        ),
      ),
    );
  }
}
