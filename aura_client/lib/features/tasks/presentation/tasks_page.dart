import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/theme/theme.dart';
import '../../auth/presentation/auth_provider.dart';
import '../data/task_repository.dart';
import 'tasks_view_model.dart';
import 'widgets/add_task_dialog.dart';
import 'widgets/empty_state_tasks.dart';
import 'widgets/task_item_card.dart';
import 'widgets/task_sync_logs_sheet.dart';

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

    return Scaffold(
      backgroundColor: AuraTheme.background,
      body: Container(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment(0.8, -0.6),
            radius: 1.2,
            colors: [
              Color(0x1F6366F1),
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
                        backgroundColor: AuraTheme.accentIndigo.withValues(alpha: 0.2),
                        child: const Icon(Icons.person, color: AuraTheme.accentIndigo),
                      ),
                    const SizedBox(width: 16),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            'Welcome back,',
                            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                  color: AuraTheme.textSecondary,
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
                      icon: const Icon(Icons.logout, color: Colors.redAccent),
                      tooltip: 'Sign Out',
                      onPressed: () => ref.read(authProvider.notifier).signOut(),
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
                        const Text(
                          'Prioritize and manage your goals',
                          style: TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
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
                            icon: const Icon(Icons.receipt_long, color: AuraTheme.textSecondary),
                            tooltip: 'View Sync Logs',
                            onPressed: () => showTaskSyncLogsSheet(context, ui.syncLogs),
                          ),
                        ui.isSyncing
                            ? Container(
                                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                                decoration: BoxDecoration(
                                  color: AuraTheme.cardBg,
                                  borderRadius: BorderRadius.circular(12),
                                  border: Border.all(color: const Color(0x1F94A3B8)),
                                ),
                                child: const Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    SizedBox(
                                      width: 16,
                                      height: 16,
                                      child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                                    ),
                                    SizedBox(width: 12),
                                    Text('Syncing...', style: TextStyle(fontSize: 14)),
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
                                      backgroundColor: hadErrors ? Colors.redAccent : AuraTheme.accentTeal,
                                    ),
                                  );
                                },
                                icon: const Icon(Icons.sync),
                                label: const Text('Sync Tasks'),
                                style: ElevatedButton.styleFrom(
                                  backgroundColor: AuraTheme.accentIndigo,
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
                        padding: const EdgeInsets.only(right: 8.0),
                        child: InkWell(
                          onTap: () => vm.setCategoryFilter(category),
                          borderRadius: BorderRadius.circular(20),
                          child: AnimatedContainer(
                            duration: const Duration(milliseconds: 200),
                            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
                            decoration: BoxDecoration(
                              gradient: isSelected ? AuraTheme.primaryGradient : null,
                              color: isSelected ? null : AuraTheme.cardBg,
                              borderRadius: BorderRadius.circular(20),
                              border: Border.all(
                                color: isSelected ? Colors.transparent : const Color(0x1F94A3B8),
                              ),
                            ),
                            child: Text(
                              category,
                              style: TextStyle(
                                color: isSelected ? Colors.white : AuraTheme.textSecondary,
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
                    loading: () => const Center(
                      child: CircularProgressIndicator(color: AuraTheme.accentIndigo),
                    ),
                    error: (err, stack) => Center(
                      child: Text(
                        'Failed to load tasks: $err',
                        style: const TextStyle(color: Colors.redAccent),
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
