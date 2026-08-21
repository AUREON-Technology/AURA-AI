import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/theme/theme.dart';
import '../health_view_model.dart';
import 'health_progress_bar.dart';

class HealthDailyDashboard extends ConsumerWidget {
  final HealthRecord? record;
  const HealthDailyDashboard({super.key, required this.record});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final water = record?.waterIntakeMl ?? 0;
    final sleep = record?.sleepMinutes ?? 0;
    final exercise = record?.exerciseMinutes ?? 0;
    final calories = record?.caloriesConsumed ?? 0;
    final weight = record?.weight;
    final vm = ref.read(healthViewModelProvider.notifier);

    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: double.infinity,
                    child: Wrap(
                      alignment: WrapAlignment.spaceBetween,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      spacing: 12,
                      runSpacing: 8,
                      children: [
                        const Text(
                          'Daily Goals Progress',
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
                              'Weight: ${weight.toStringAsFixed(1)} kg',
                              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: Colors.white),
                            ),
                          ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 20),
                  HealthProgressBar(
                    title: 'Water Intake',
                    current: water,
                    target: HealthGoals.waterGoalMl,
                    unit: 'ml',
                    color: Colors.cyan,
                    icon: Icons.local_drink_outlined,
                  ),
                  const SizedBox(height: 16),
                  HealthProgressBar(
                    title: 'Sleep Duration',
                    current: sleep,
                    target: HealthGoals.sleepGoalMins,
                    unit: 'mins',
                    color: AuraTheme.accentIndigo,
                    icon: Icons.nightlight_round_outlined,
                    customSubtitle: '${(sleep / 60).toStringAsFixed(1)} / 8.0 hrs',
                  ),
                  const SizedBox(height: 16),
                  HealthProgressBar(
                    title: 'Exercise Duration',
                    current: exercise,
                    target: HealthGoals.exerciseGoalMins,
                    unit: 'mins',
                    color: AuraTheme.accentAmber,
                    icon: Icons.fitness_center_outlined,
                  ),
                  const SizedBox(height: 16),
                  HealthProgressBar(
                    title: 'Calories Consumed',
                    current: calories,
                    target: HealthGoals.calorieGoalKcal,
                    unit: 'kcal',
                    color: Colors.pinkAccent,
                    icon: Icons.restaurant_outlined,
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 24),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20.0),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Quick Logger Panel',
                    style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white),
                  ),
                  const SizedBox(height: 16),
                  Wrap(
                    spacing: 12,
                    runSpacing: 12,
                    children: [
                      ElevatedButton.icon(
                        onPressed: () => vm.logWater(250),
                        icon: const Icon(Icons.add, size: 16),
                        label: const Text('+250ml Water'),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: Colors.cyan,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () => vm.logWater(500),
                        icon: const Icon(Icons.add, size: 16),
                        label: const Text('+500ml Water'),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: Colors.cyan.shade700,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () {
                          showHealthLogDialog(
                            context: context,
                            title: 'Log Sleep Duration',
                            labelText: 'Sleep Minutes',
                            hintText: 'e.g. 480 for 8 hours',
                            keyboardType: TextInputType.number,
                            onSubmit: (input) {
                              final mins = int.tryParse(input);
                              if (mins != null && mins > 0) vm.logSleep(mins);
                            },
                          );
                        },
                        icon: const Icon(Icons.nightlight_outlined, size: 16),
                        label: const Text('Log Sleep'),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: AuraTheme.accentIndigo,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () {
                          showHealthLogDialog(
                            context: context,
                            title: 'Log Exercise Duration',
                            labelText: 'Exercise Minutes',
                            hintText: 'e.g. 30',
                            keyboardType: TextInputType.number,
                            onSubmit: (input) {
                              final mins = int.tryParse(input);
                              if (mins != null && mins > 0) vm.logExercise(mins);
                            },
                          );
                        },
                        icon: const Icon(Icons.fitness_center, size: 16),
                        label: const Text('Log Exercise'),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: AuraTheme.accentAmber,
                          foregroundColor: Colors.black87,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () {
                          showHealthLogDialog(
                            context: context,
                            title: 'Log Calories',
                            labelText: 'Kcal Consumed',
                            hintText: 'e.g. 500',
                            keyboardType: TextInputType.number,
                            onSubmit: (input) {
                              final kcal = int.tryParse(input);
                              if (kcal != null && kcal > 0) vm.logCalories(kcal);
                            },
                          );
                        },
                        icon: const Icon(Icons.restaurant, size: 16),
                        label: const Text('Log Calories'),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: Colors.pinkAccent,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () {
                          showHealthLogDialog(
                            context: context,
                            title: 'Log Current Weight',
                            labelText: 'Weight (kg)',
                            hintText: 'e.g. 74.5',
                            keyboardType: const TextInputType.numberWithOptions(decimal: true),
                            onSubmit: (input) {
                              final w = double.tryParse(input);
                              if (w != null && w > 0) vm.logWeight(w);
                            },
                          );
                        },
                        icon: const Icon(Icons.monitor_weight_outlined, size: 16),
                        label: const Text('Log Weight'),
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
}
