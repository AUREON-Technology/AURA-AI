import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/theme/theme.dart';
import '../tasks_view_model.dart';

class TaskItemCard extends ConsumerWidget {
  final Task task;
  const TaskItemCard({super.key, required this.task});

  Color _getPriorityColor(String priority) {
    switch (priority.toLowerCase()) {
      case 'high':
        return Colors.redAccent;
      case 'medium':
        return AuraTheme.accentAmber;
      case 'low':
      default:
        return AuraTheme.accentTeal;
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final hasDueDate = task.dueDate != null;
    final formattedDate = hasDueDate ? DateFormat('MMM d, y').format(task.dueDate!) : null;
    final isOverdue = hasDueDate &&
        task.dueDate!.isBefore(DateTime.now().subtract(const Duration(days: 1))) &&
        !task.isCompleted;
    final vm = ref.read(tasksViewModelProvider.notifier);

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Row(
          children: [
            IconButton(
              icon: Icon(
                task.isCompleted ? Icons.check_circle : Icons.radio_button_unchecked,
                color: task.isCompleted ? AuraTheme.accentTeal : AuraTheme.textSecondary,
                size: 26,
              ),
              onPressed: () => vm.toggleComplete(task.id, task.isCompleted),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    task.title,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                      color: task.isCompleted ? AuraTheme.textSecondary.withValues(alpha: 0.6) : AuraTheme.textPrimary,
                      decoration: task.isCompleted ? TextDecoration.lineThrough : null,
                    ),
                  ),
                  if (task.description != null && task.description!.isNotEmpty) ...[
                    const SizedBox(height: 4),
                    Text(
                      task.description!,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 13,
                        color: task.isCompleted ? AuraTheme.textSecondary.withValues(alpha: 0.4) : AuraTheme.textSecondary,
                      ),
                    ),
                  ],
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    runSpacing: 4,
                    children: [
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                        decoration: BoxDecoration(
                          color: const Color(0x0DFFFFFF),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Text(
                          task.category.toUpperCase(),
                          style: const TextStyle(fontSize: 10, color: AuraTheme.textSecondary, fontWeight: FontWeight.bold),
                        ),
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                        decoration: BoxDecoration(
                          color: _getPriorityColor(task.priority).withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Text(
                          task.priority.toUpperCase(),
                          style: TextStyle(
                            fontSize: 10,
                            color: _getPriorityColor(task.priority),
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                      if (hasDueDate)
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                          decoration: BoxDecoration(
                            color: isOverdue ? Colors.redAccent.withValues(alpha: 0.15) : const Color(0x0DFFFFFF),
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(
                                Icons.calendar_today,
                                size: 10,
                                color: isOverdue ? Colors.redAccent : AuraTheme.textSecondary,
                              ),
                              const SizedBox(width: 4),
                              Text(
                                formattedDate!,
                                style: TextStyle(
                                  fontSize: 10,
                                  color: isOverdue ? Colors.redAccent : AuraTheme.textSecondary,
                                  fontWeight: isOverdue ? FontWeight.bold : FontWeight.normal,
                                ),
                              ),
                            ],
                          ),
                        ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            IconButton(
              icon: const Icon(Icons.delete_outline, color: Colors.white24, size: 20),
              hoverColor: Colors.redAccent.withValues(alpha: 0.1),
              onPressed: () => vm.deleteTask(task.id),
            ),
          ],
        ),
      ),
    );
  }
}
