import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import '../../auth/presentation/auth_provider.dart';
import '../../auth/presentation/sign_out_confirmation.dart';
import '../data/task_repository.dart';
import 'tasks_view_model.dart';
import 'widgets/add_task_dialog.dart';
import 'widgets/empty_state_tasks.dart';
import 'widgets/task_item_card.dart';
import 'widgets/task_sync_logs_sheet.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';

class TasksPage extends ConsumerWidget {
  const TasksPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final authState = ref.watch(authProvider);
    final tasksAsync = ref.watch(tasksStreamProvider);
    final currentFilter = ref.watch(taskCategoryFilterProvider);
    final ui = ref.watch(tasksViewModelProvider);
    final vm = ref.read(tasksViewModelProvider.notifier);
    final user = authState.value;
    final palette = AuraPalette.of(context);

    return Scaffold(
      backgroundColor: palette.background,
      body: Container(
        decoration: BoxDecoration(
          gradient: RadialGradient(
            center: const Alignment(0.8, -0.6),
            radius: 1.2,
            colors: [
              palette.brand.withValues(alpha: 0.12),
              Colors.transparent,
            ],
          ),
        ),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    if (user?.photoUrl != null)
                      CircleAvatar(
                        radius: 24,
                        backgroundImage: NetworkImage(user!.photoUrl!),
                      )
                    else
                      CircleAvatar(
                        radius: 24,
                        backgroundColor: palette.brand.withValues(alpha: 0.2),
                        child: Icon(Icons.person, color: palette.brandOnSurfaceText),
                      ),
                    const SizedBox(width: 16),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'Welcome back,',
                            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                  color: palette.textSecondary,
                                ),
                          ),
                          Text(
                            user?.displayName ?? 'Aura User',
                            style: Theme.of(context).textTheme.titleLarge?.copyWith(
                                  fontWeight: FontWeight.bold,
                                ),
                          ),
                        ],
                      ),
                    ),
                    IconButton(
                      icon: Icon(Icons.logout, color: palette.danger),
                      tooltip: 'Sign Out',
                      onPressed: () => confirmAndSignOut(context, ref),
                    ),
                  ],
                ),
                const SizedBox(height: 32),
                Wrap(
                  spacing: 12,
                  runSpacing: 12,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  alignment: WrapAlignment.spaceBetween,
                  children: [
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Task Workspace',
                          style: Theme.of(context).textTheme.displayLarge?.copyWith(
                                fontSize: 28,
                                fontWeight: FontWeight.w900,
                              ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          'Prioritize and manage your goals',
                          style: TextStyle(color: palette.textSecondary, fontSize: 13),
                        ),
                      ],
                    ),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        if (ui.syncLogs.isNotEmpty)
                          IconButton(
                            icon: Icon(Icons.receipt_long, color: palette.textSecondary),
                            tooltip: 'View Sync Logs',
                            onPressed: () => showTaskSyncLogsSheet(context, ui.syncLogs),
                          ),
                        ui.isSyncing
                            ? Container(
                                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                                decoration: BoxDecoration(
                                  color: palette.surfaceVariant,
                                  borderRadius: BorderRadius.circular(12),
                                  border: Border.all(color: palette.border),
                                ),
                                child: Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    SizedBox(
                                      width: 16,
                                      height: 16,
                                      child: CircularProgressIndicator(strokeWidth: 2, color: palette.brandOnSurfaceText),
                                    ),
                                    const SizedBox(width: 12),
                                    Text('Syncing...', style: TextStyle(fontSize: 14, color: palette.textPrimary)),
                                  ],
                                ),
                              )
                            : ElevatedButton.icon(
                                onPressed: () async {
                                  final logs = await vm.syncGoogleTasks();
                                  if (logs == null || !context.mounted) return;
                                  final hadErrors = logs.any((l) => l.contains('error'));
                                  ScaffoldMessenger.of(context).showSnackBar(
                                    SnackBar(
                                      content: Text(
                                        hadErrors
                                            ? 'Tasks sync encountered errors.'
                                            : 'Google Tasks sync completed successfully!',
                                      ),
                                      backgroundColor: hadErrors ? palette.danger : palette.success,
                                    ),
                                  );
                                },
                                icon: const Icon(Icons.sync),
                                label: const Text('Sync Tasks'),
                                style: ElevatedButton.styleFrom(
                                  backgroundColor: palette.brand,
                                  foregroundColor: palette.onBrand,
                                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                                ),
                              ),
                        ElevatedButton.icon(
                          onPressed: () {
                            showDialog(
                              context: context,
                              builder: (context) => const AddTaskDialog(),
                            );
                          },
                          icon: const Icon(Icons.add, size: 20),
                          label: const Text('Add Task'),
                          style: ElevatedButton.styleFrom(
                            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
                const SizedBox(height: 24),
                SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(
                    children: ['All', 'Personal', 'Academic', 'Client', 'Project'].map((category) {
                      final isSelected = currentFilter == category;
                      return Padding(
                        padding: const EdgeInsetsDirectional.only(end: 8.0),
                        child: InkWell(
                          onTap: () => vm.setCategoryFilter(category),
                          borderRadius: BorderRadius.circular(20),
                          child: AnimatedContainer(
                            duration: const Duration(milliseconds: 200),
                            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
                            decoration: BoxDecoration(
                              gradient: isSelected ? palette.brandGradient : null,
                              color: isSelected ? null : palette.surfaceVariant,
                              borderRadius: BorderRadius.circular(20),
                              border: Border.all(
                                color: isSelected ? Colors.transparent : palette.border,
                              ),
                            ),
                            child: Text(
                              category,
                              style: TextStyle(
                                color: isSelected ? palette.onBrand : palette.textSecondary,
                                fontWeight: isSelected ? FontWeight.w600 : FontWeight.normal,
                              ),
                            ),
                          ),
                        ),
                      );
                    }).toList(),
                  ),
                ),
                const SizedBox(height: 24),
                Expanded(
                  child: tasksAsync.when(
                    data: (tasks) {
                      if (tasks.isEmpty) {
                        return const Center(child: EmptyStateTasks());
                      }
                      return ListView.builder(
                        itemCount: tasks.length,
                        itemBuilder: (context, index) {
                          return TaskItemCard(task: tasks[index]);
                        },
                      );
                    },
                    loading: () => Center(
                      child: CircularProgressIndicator(color: palette.brand),
                    ),
                    error: (err, stack) => Center(
                      child: SingleChildScrollView(
                        child: AuraFailureView(failure: AuraFailure.from(err)),
                      ),
                    ),
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
