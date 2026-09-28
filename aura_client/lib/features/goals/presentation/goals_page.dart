import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/theme/theme.dart';
import '../../auth/presentation/auth_provider.dart';
import '../data/goal_providers.dart';
import 'goals_view_model.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';

class GoalsPage extends ConsumerWidget {
  const GoalsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final authState = ref.watch(authProvider);
    final goalsAsync = ref.watch(goalsStreamProvider);
    ref.watch(goalsViewModelProvider);
    final vm = ref.read(goalsViewModelProvider.notifier);
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
                          'Goals',
                          style: Theme.of(context).textTheme.displayLarge?.copyWith(
                                fontSize: 28,
                                fontWeight: FontWeight.w900,
                              ),
                        ),
                        Text(
                          'Track your aspirations and progress',
                          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                color: AuraTheme.textSecondary,
                              ),
                        ),
                      ],
                    ),
                    FilledButton.icon(
                      onPressed: () => _showAddGoalDialog(context, ref, vm),
                      icon: const Icon(Icons.add),
                      label: const Text('New Goal'),
                    ),
                  ],
                ),
                const SizedBox(height: 24),
                Expanded(
                  child: goalsAsync.when(
                    data: (goals) {
                      if (goals.isEmpty) {
                        return const Center(
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Icon(Icons.flag, size: 64, color: Colors.grey),
                              SizedBox(height: 16),
                              Text('No goals yet', style: TextStyle(fontSize: 18, color: Colors.grey)),
                              SizedBox(height: 8),
                              Text('Create your first goal to get started', style: TextStyle(color: Colors.grey)),
                            ],
                          ),
                        );
                      }
                      return ListView.builder(
                        itemCount: goals.length,
                        itemBuilder: (context, index) {
                          final goal = goals[index];
                          return _GoalCard(goal: goal, onDelete: () => vm.deleteGoal(goal.id));
                        },
                      );
                    },
                    loading: () => const Center(child: CircularProgressIndicator()),
                    error: (e, st) => Center(
                      child: SingleChildScrollView(
                        child: AuraFailureView(
                          failure: AuraFailure.from(e),
                          onRetry: () => ref.invalidate(goalsStreamProvider),
                        ),
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

  void _showAddGoalDialog(BuildContext context, WidgetRef ref, GoalsViewModel vm) {
    final titleController = TextEditingController();
    final descController = TextEditingController();
    String status = 'active';
    String priority = 'medium';
    DateTime? targetDate;
    int progressPct = 0;

    showDialog(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Create Goal'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(
                  controller: titleController,
                  decoration: const InputDecoration(labelText: 'Title *'),
                  autofocus: true,
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: descController,
                  decoration: const InputDecoration(labelText: 'Description'),
                  maxLines: 3,
                ),
                const SizedBox(height: 16),
                DropdownButtonFormField<String>(
                  initialValue: status,
                  items: ['active', 'achieved', 'abandoned'].map((s) => DropdownMenuItem(value: s, child: Text(s))).toList(),
                  onChanged: (v) => setState(() => status = v!),
                  decoration: const InputDecoration(labelText: 'Status'),
                ),
                const SizedBox(height: 16),
                DropdownButtonFormField<String>(
                  initialValue: priority,
                  items: ['low', 'medium', 'high'].map((s) => DropdownMenuItem(value: s, child: Text(s))).toList(),
                  onChanged: (v) => setState(() => priority = v!),
                  decoration: const InputDecoration(labelText: 'Priority'),
                ),
                const SizedBox(height: 16),
                ListTile(
                  title: Text(targetDate == null ? 'Set target date (optional)' : 'Target: ${targetDate!.toLocal().toString().split(' ')[0]}'),
                  trailing: const Icon(Icons.calendar_today),
                  onTap: () async {
                    final date = await showDatePicker(context: context, initialDate: DateTime.now(), firstDate: DateTime.now(), lastDate: DateTime.now().add(const Duration(days: 365 * 5)));
                    if (date != null) setState(() => targetDate = date);
                  },
                ),
                const SizedBox(height: 16),
                Text('Progress: $progressPct%'),
                Slider(
                  value: progressPct.toDouble(),
                  min: 0,
                  max: 100,
                  divisions: 20,
                  onChanged: (v) => setState(() => progressPct = v.round()),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
            FilledButton(
              onPressed: () async {
                if (titleController.text.trim().isEmpty) return;
                await vm.addGoal(
                  title: titleController.text.trim(),
                  description: descController.text.trim().isEmpty ? null : descController.text.trim(),
                  status: status,
                  priority: priority,
                  targetDate: targetDate,
                  progressPct: progressPct,
                );
                if (context.mounted) Navigator.pop(context);
              },
              child: const Text('Create'),
            ),
          ],
        ),
      ),
    );
  }
}

class _GoalCard extends StatelessWidget {
  final Goal goal;
  final VoidCallback onDelete;

  const _GoalCard({required this.goal, required this.onDelete});

  Color _statusColor(String status) {
    switch (status) {
      case 'achieved':
        return Colors.green;
      case 'abandoned':
        return Colors.grey;
      default:
        return Colors.blue;
    }
  }

  Color _priorityColor(String priority) {
    switch (priority) {
      case 'high':
        return Colors.red;
      case 'low':
        return Colors.green;
      default:
        return Colors.orange;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                CircleAvatar(
                  backgroundColor: _statusColor(goal.status).withValues(alpha: 0.2),
                  child: Icon(Icons.flag, color: _statusColor(goal.status)),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(goal.title, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 16)),
                      if (goal.description != null && goal.description!.isNotEmpty)
                        Text(goal.description!, maxLines: 2, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 13, color: Colors.grey[600])),
                    ],
                  ),
                ),
                PopupMenuButton<String>(
                  onSelected: (value) {
                    if (value == 'delete') onDelete();
                  },
                  itemBuilder: (context) => [
                    const PopupMenuItem(value: 'edit', child: Text('Edit')),
                    const PopupMenuItem(value: 'delete', child: Text('Delete', style: TextStyle(color: Colors.red))),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                if (goal.category != null && goal.category!.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: Chip(
                      label: Text(goal.category!, style: const TextStyle(fontSize: 11)),
                      backgroundColor: Colors.blue.withValues(alpha: 0.1),
                    ),
                  ),
                Chip(
                  label: Text(goal.status, style: TextStyle(fontSize: 11, color: _statusColor(goal.status))),
                  backgroundColor: _statusColor(goal.status).withValues(alpha: 0.1),
                ),
                const SizedBox(width: 8),
                Chip(
                  label: Text(goal.priority, style: TextStyle(fontSize: 11, color: _priorityColor(goal.priority))),
                  backgroundColor: _priorityColor(goal.priority).withValues(alpha: 0.1),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: LinearProgressIndicator(
                    value: goal.progressPct / 100,
                    backgroundColor: Colors.grey[200],
                    valueColor: AlwaysStoppedAnimation<Color>(_statusColor(goal.status)),
                    minHeight: 8,
                  ),
                ),
                const SizedBox(width: 12),
                Text('${goal.progressPct}%', style: const TextStyle(fontWeight: FontWeight.w600)),
              ],
            ),
            if (goal.targetDate != null) ...[
              const SizedBox(height: 8),
              Text('Target: ${goal.targetDate!.toLocal().toString().split(' ')[0]}', style: TextStyle(fontSize: 12, color: Colors.grey[600])),
            ],
          ],
        ),
      ),
    );
  }
}