import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/undo/undoable_deletes.dart';
import '../tasks_view_model.dart';
import 'package:aura_client/l10n/l10n.dart';

class TaskItemCard extends ConsumerWidget {
  final Task task;
  const TaskItemCard({super.key, required this.task});

  Color _getPriorityColor(AuraPalette palette, String priority) {
    switch (priority.toLowerCase()) {
      case 'high':
        return palette.danger;
      case 'medium':
        return palette.warning;
      case 'low':
      default:
        return palette.success;
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Theme-aware: the legacy `AuraTheme` text colours are dark-theme-only and rendered the
    // title at ~1.05:1 on the light theme's white card (audit F-32).
    final palette = AuraPalette.of(context);
    final priorityColor = _getPriorityColor(palette, task.priority);
    final hasDueDate = task.dueDate != null;
    final formattedDate = hasDueDate ? DateFormat.yMMMd().format(task.dueDate!) : null;
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
              tooltip: task.isCompleted ? context.l10n.taskMarkNotDone : context.l10n.taskMarkDone,
              icon: Icon(
                task.isCompleted ? Icons.check_circle : Icons.radio_button_unchecked,
                color: task.isCompleted ? palette.success : palette.textSecondary,
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
                      color: task.isCompleted ? palette.textDisabled : palette.textPrimary,
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
                        color: task.isCompleted ? palette.textDisabled : palette.textSecondary,
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
                          color: palette.surfaceVariant,
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Text(
                          taskCategoryLabel(context.l10n, task.category).toUpperCase(),
                          style: TextStyle(fontSize: 10, color: palette.textSecondary, fontWeight: FontWeight.bold),
                        ),
                      ),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                        decoration: BoxDecoration(
                          color: priorityColor.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Text(
                          taskPriorityLabel(context.l10n, task.priority).toUpperCase(),
                          style: TextStyle(
                            fontSize: 10,
                            color: priorityColor,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                      if (hasDueDate)
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                          decoration: BoxDecoration(
                            color: isOverdue ? palette.danger.withValues(alpha: 0.15) : palette.surfaceVariant,
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(
                                Icons.calendar_today,
                                size: 10,
                                color: isOverdue ? palette.danger : palette.textSecondary,
                              ),
                              const SizedBox(width: 4),
                              Text(
                                formattedDate!,
                                style: TextStyle(
                                  fontSize: 10,
                                  color: isOverdue ? palette.danger : palette.textSecondary,
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
              icon: Icon(Icons.delete_outline, color: palette.textSecondary, size: 20),
              hoverColor: palette.danger.withValues(alpha: 0.1),
              tooltip: context.l10n.tasksDeleteTask,
              // Hidden now, deleted once the Undo window closes (F-34).
              onPressed: () => deleteWithUndo(
                context,
                ref,
                key: taskUndoKey(task.id),
                message: context.l10n.tasksTaskDeleted,
                commit: () => vm.deleteTask(task.id),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
