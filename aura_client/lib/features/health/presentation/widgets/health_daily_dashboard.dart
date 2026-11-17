import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/theme/theme.dart';
import '../health_view_model.dart';
import 'health_progress_bar.dart';
import 'package:aura_client/l10n/l10n.dart';

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
    // Theme-aware text: hardcoded white was near-invisible on the light theme's white card (F-32).
    final palette = AuraPalette.of(context);

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
                        Text(
                          context.l10n.healthDailyGoalsTitle,
                          style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: palette.textPrimary),
                        ),
                        if (weight != null)
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                            decoration: BoxDecoration(
                              color: palette.brand.withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(20),
                              border: Border.all(color: palette.brand.withValues(alpha: 0.3)),
                            ),
                            child: Text(
                              context.l10n.healthWeightBadge(weight.toStringAsFixed(1)),
                              style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: palette.textPrimary),
                            ),
                          ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 20),
                  HealthProgressBar(
                    title: context.l10n.healthWaterIntake,
                    current: water,
                    target: HealthGoals.waterGoalMl,
                    unit: context.l10n.healthUnitMl,
                    color: Colors.cyan,
                    icon: Icons.local_drink_outlined,
                  ),
                  const SizedBox(height: 16),
                  HealthProgressBar(
                    title: context.l10n.healthSleepDuration,
                    current: sleep,
                    target: HealthGoals.sleepGoalMins,
                    unit: context.l10n.healthUnitMins,
                    color: palette.brand,
                    icon: Icons.nightlight_round_outlined,
                    customSubtitle: context.l10n.healthSleepHoursProgress((sleep / 60).toStringAsFixed(1)),
                  ),
                  const SizedBox(height: 16),
                  HealthProgressBar(
                    title: context.l10n.healthExerciseDuration,
                    current: exercise,
                    target: HealthGoals.exerciseGoalMins,
                    unit: context.l10n.healthUnitMins,
                    color: palette.warning,
                    icon: Icons.fitness_center_outlined,
                  ),
                  const SizedBox(height: 16),
                  HealthProgressBar(
                    title: context.l10n.healthCaloriesConsumed,
                    current: calories,
                    target: HealthGoals.calorieGoalKcal,
                    unit: context.l10n.healthUnitKcal,
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
                  Text(
                    context.l10n.healthQuickLoggerTitle,
                    style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: palette.textPrimary),
                  ),
                  const SizedBox(height: 16),
                  Wrap(
                    spacing: 12,
                    runSpacing: 12,
                    children: [
                      ElevatedButton.icon(
                        onPressed: () => vm.logWater(250),
                        icon: const Icon(Icons.add, size: 16),
                        label: Text(context.l10n.healthAddWater250),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: Colors.cyan,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () => vm.logWater(500),
                        icon: const Icon(Icons.add, size: 16),
                        label: Text(context.l10n.healthAddWater500),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: Colors.cyan.shade700,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () {
                          showHealthLogDialog(
                            context: context,
                            title: context.l10n.healthLogSleepTitle,
                            labelText: context.l10n.healthSleepMinutesLabel,
                            hintText: context.l10n.healthSleepMinutesHint,
                            keyboardType: TextInputType.number,
                            onSubmit: (input) {
                              final mins = int.tryParse(input);
                              if (mins != null && mins > 0) vm.logSleep(mins);
                            },
                          );
                        },
                        icon: const Icon(Icons.nightlight_outlined, size: 16),
                        label: Text(context.l10n.healthLogSleep),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: palette.brand,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () {
                          showHealthLogDialog(
                            context: context,
                            title: context.l10n.healthLogExerciseTitle,
                            labelText: context.l10n.healthExerciseMinutesLabel,
                            hintText: context.l10n.healthExampleHint('30'),
                            keyboardType: TextInputType.number,
                            onSubmit: (input) {
                              final mins = int.tryParse(input);
                              if (mins != null && mins > 0) vm.logExercise(mins);
                            },
                          );
                        },
                        icon: const Icon(Icons.fitness_center, size: 16),
                        label: Text(context.l10n.healthLogExercise),
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
                            title: context.l10n.healthLogCaloriesTitle,
                            labelText: context.l10n.healthKcalConsumedLabel,
                            hintText: context.l10n.healthExampleHint('500'),
                            keyboardType: TextInputType.number,
                            onSubmit: (input) {
                              final kcal = int.tryParse(input);
                              if (kcal != null && kcal > 0) vm.logCalories(kcal);
                            },
                          );
                        },
                        icon: const Icon(Icons.restaurant, size: 16),
                        label: Text(context.l10n.healthLogCalories),
                        style: ElevatedButton.styleFrom(
                          backgroundColor: Colors.pinkAccent,
                          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        ),
                      ),
                      ElevatedButton.icon(
                        onPressed: () {
                          showHealthLogDialog(
                            context: context,
                            title: context.l10n.healthLogWeightTitle,
                            labelText: context.l10n.healthWeightKgLabel,
                            hintText: context.l10n.healthExampleHint('74.5'),
                            keyboardType: const TextInputType.numberWithOptions(decimal: true),
                            onSubmit: (input) {
                              final w = double.tryParse(input);
                              if (w != null && w > 0) vm.logWeight(w);
                            },
                          );
                        },
                        icon: const Icon(Icons.monitor_weight_outlined, size: 16),
                        label: Text(context.l10n.healthLogWeight),
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
