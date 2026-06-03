import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';
import '../data/task_repository.dart';
import '../../auth/presentation/auth_provider.dart';
import '../../../core/theme/theme.dart';
import '../../../core/database/database.dart';

class TasksPage extends ConsumerStatefulWidget {
  const TasksPage({super.key});

  @override
  ConsumerState<TasksPage> createState() => _TasksPageState();
}

class _TasksPageState extends ConsumerState<TasksPage> {
  bool _isSyncing = false;
  List<String> _syncLogs = [];

  void _showAddTaskDialog() {
    showDialog(
      context: context,
      builder: (context) => const AddTaskDialog(),
    );
  }

  Future<void> _syncTasks() async {
    setState(() {
      _isSyncing = true;
      _syncLogs = ["Starting synchronization..."];
    });

    final auth = ref.read(authProvider).value;
    final isMock = auth?.id == 'mock_user_123' || auth == null;

    final logs = await ref.read(taskRepositoryProvider).syncGoogleTasks(isMock: isMock);

    if (mounted) {
      setState(() {
        _syncLogs = logs;
        _isSyncing = false;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            logs.any((l) => l.contains("error")) 
                ? "Tasks sync encountered errors." 
                : "Google Tasks sync completed successfully!",
          ),
          backgroundColor: logs.any((l) => l.contains("error")) 
              ? Colors.redAccent 
              : AuraTheme.accentTeal,
        ),
      );
    }
  }

  void _showSyncLogs() {
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF0F0C1B),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (context) {
        return Container(
          padding: const EdgeInsets.all(24),
          decoration: BoxDecoration(
            color: AuraTheme.cardBg,
            borderRadius: const BorderRadius.vertical(top: Radius.circular(24)),
            border: Border.all(color: const Color(0x1F94A3B8)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text("Tasks Sync Activity Log", style: Theme.of(context).textTheme.titleLarge),
                  IconButton(
                    icon: const Icon(Icons.close, color: Colors.white),
                    onPressed: () => Navigator.pop(context),
                  ),
                ],
              ),
              const Divider(color: Color(0x1F94A3B8), height: 24),
              Expanded(
                child: _syncLogs.isEmpty
                    ? const Center(
                        child: Text(
                          "No logs recorded yet. Press sync to begin.",
                          style: TextStyle(color: AuraTheme.textSecondary),
                        ),
                      )
                    : ListView.builder(
                        itemCount: _syncLogs.length,
                        itemBuilder: (context, index) {
                          final log = _syncLogs[index];
                          IconData logIcon = Icons.info_outline;
                          Color logColor = AuraTheme.textSecondary;

                          if (log.toLowerCase().contains("error") || log.toLowerCase().contains("failed")) {
                            logIcon = Icons.error_outline;
                            logColor = Colors.redAccent;
                          } else if (log.toLowerCase().contains("success")) {
                            logIcon = Icons.check_circle_outline;
                            logColor = AuraTheme.accentTeal;
                          } else if (log.toLowerCase().contains("import") || log.toLowerCase().contains("upload")) {
                            logIcon = Icons.cloud_sync_outlined;
                            logColor = AuraTheme.accentIndigo;
                          }

                          return Padding(
                            padding: const EdgeInsets.symmetric(vertical: 6.0),
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Icon(logIcon, color: logColor, size: 18),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Text(
                                    log,
                                    style: TextStyle(color: logColor, fontSize: 13, fontFamily: 'Courier'),
                                  ),
                                ),
                              ],
                            ),
                          );
                        },
                      ),
              ),
            ],
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final authState = ref.watch(authProvider);
    final tasksAsync = ref.watch(tasksStreamProvider);
    final currentFilter = ref.watch(taskCategoryFilterProvider);

    final user = authState.value;

    return Scaffold(
      backgroundColor: AuraTheme.background,
      body: Container(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment(0.8, -0.6),
            radius: 1.2,
            colors: [
              Color(0x1F6366F1), // Translucent indigo
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
                // Header Panel
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
                      onPressed: () {
                        ref.read(authProvider.notifier).signOut();
                      },
                    ),
                  ],
                ),
                const SizedBox(height: 32),

                // Greeting & Add Button Row
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Task Workspace',
                          style: Theme.of(context).textTheme.displayLarge?.copyWith(
                                fontSize: 28,
                                fontWeight: FontWeight.w900,
                                // Keep exact height or spacing if needed
                              ),
                        ),
                        const SizedBox(height: 4),
                        const Text(
                          'Prioritize and manage your goals',
                          style: TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
                        ),
                      ],
                    ),
                    Row(
                      children: [
                        if (_syncLogs.isNotEmpty)
                          IconButton(
                            icon: const Icon(Icons.receipt_long, color: AuraTheme.textSecondary),
                            tooltip: "View Sync Logs",
                            onPressed: _showSyncLogs,
                          ),
                        const SizedBox(width: 8),
                        _isSyncing
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
                                    Text("Syncing...", style: TextStyle(fontSize: 14)),
                                  ],
                                ),
                              )
                            : ElevatedButton.icon(
                                onPressed: _syncTasks,
                                icon: const Icon(Icons.sync),
                                label: const Text("Sync Tasks"),
                                style: ElevatedButton.styleFrom(
                                  backgroundColor: AuraTheme.accentIndigo,
                                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                                ),
                              ),
                        const SizedBox(width: 12),
                        ElevatedButton.icon(
                          onPressed: _showAddTaskDialog,
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

                // Category Chips Selector
                SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(
                    children: ['All', 'Personal', 'Academic', 'Client', 'Project'].map((category) {
                      final isSelected = currentFilter == category;
                      return Padding(
                        padding: const EdgeInsets.only(right: 8.0),
                        child: InkWell(
                          onTap: () {
                            ref.read(taskCategoryFilterProvider.notifier).setFilter(category);
                          },
                          borderRadius: BorderRadius.circular(20),
                          child: AnimatedContainer(
                            duration: const Duration(milliseconds: 200),
                            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
                            decoration: BoxDecoration(
                              gradient: isSelected ? AuraTheme.primaryGradient : null,
                              color: isSelected ? null : AuraTheme.cardBg,
                              borderRadius: BorderRadius.circular(20),
                              border: Border.all(
                                color: isSelected
                                    ? Colors.transparent
                                    : const Color(0x1F94A3B8),
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

                // Task List Content
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

// ----------------------------------------------------
// Task Card Item
// ----------------------------------------------------
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
    final formattedDate = hasDueDate
        ? DateFormat('MMM d, y').format(task.dueDate!)
        : null;

    final isOverdue = hasDueDate &&
        task.dueDate!.isBefore(DateTime.now().subtract(const Duration(days: 1))) &&
        !task.isCompleted;

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Row(
          children: [
            // Checkbox
            IconButton(
              icon: Icon(
                task.isCompleted
                    ? Icons.check_circle
                    : Icons.radio_button_unchecked,
                color: task.isCompleted
                    ? AuraTheme.accentTeal
                    : AuraTheme.textSecondary,
                size: 26,
              ),
              onPressed: () {
                ref
                    .read(taskRepositoryProvider)
                    .toggleTaskComplete(task.id, task.isCompleted);
              },
            ),
            const SizedBox(width: 8),

            // Task Meta & Info
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    task.title,
                    style: TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                      color: task.isCompleted
                          ? AuraTheme.textSecondary.withValues(alpha: 0.6)
                          : AuraTheme.textPrimary,
                      decoration: task.isCompleted
                          ? TextDecoration.lineThrough
                          : null,
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
                        color: task.isCompleted
                            ? AuraTheme.textSecondary.withValues(alpha: 0.4)
                            : AuraTheme.textSecondary,
                      ),
                    ),
                  ],
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    runSpacing: 4,
                    children: [
                      // Category Pill
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
                      // Priority Pill
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
                      // Due Date Pill
                      if (hasDueDate)
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                          decoration: BoxDecoration(
                            color: isOverdue
                                ? Colors.redAccent.withValues(alpha: 0.15)
                                : const Color(0x0DFFFFFF),
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

            // Delete Button
            IconButton(
              icon: const Icon(Icons.delete_outline, color: Colors.white24, size: 20),
              hoverColor: Colors.redAccent.withValues(alpha: 0.1),
              onPressed: () {
                ref.read(taskRepositoryProvider).deleteTask(task.id);
              },
            ),
          ],
        ),
      ),
    );
  }
}

// ----------------------------------------------------
// Empty State
// ----------------------------------------------------
class EmptyStateTasks extends StatelessWidget {
  const EmptyStateTasks({super.key});

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Icon(
          Icons.task_alt,
          size: 72,
          color: AuraTheme.textSecondary.withValues(alpha: 0.3),
        ),
        const SizedBox(height: 16),
        const Text(
          'All caught up!',
          style: TextStyle(
            color: AuraTheme.textPrimary,
            fontSize: 18,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 4),
        const Text(
          'Enjoy your day or create a new task to stay organized.',
          style: TextStyle(
            color: AuraTheme.textSecondary,
            fontSize: 14,
          ),
          textAlign: TextAlign.center,
        ),
      ],
    );
  }
}

// ----------------------------------------------------
// Dialog: Add Task
// ----------------------------------------------------
class AddTaskDialog extends ConsumerStatefulWidget {
  const AddTaskDialog({super.key});

  @override
  ConsumerState<AddTaskDialog> createState() => _AddTaskDialogState();
}

class _AddTaskDialogState extends ConsumerState<AddTaskDialog> {
  final _formKey = GlobalKey<FormState>();
  final _titleController = TextEditingController();
  final _descController = TextEditingController();

  String _priority = 'medium';
  String _category = 'personal';
  DateTime? _dueDate;

  @override
  void dispose() {
    _titleController.dispose();
    _descController.dispose();
    super.dispose();
  }

  Future<void> _selectDate(BuildContext context) async {
    final DateTime? picked = await showDatePicker(
      context: context,
      initialDate: DateTime.now(),
      firstDate: DateTime.now(),
      lastDate: DateTime.now().add(const Duration(days: 365)),
      builder: (context, child) {
        return Theme(
          data: Theme.of(context).copyWith(
            colorScheme: const ColorScheme.dark(
              primary: AuraTheme.accentIndigo,
              onPrimary: Colors.white,
              surface: Color(0xFF1E1B4B),
              onSurface: Colors.white,
            ),
            dialogTheme: const DialogThemeData(
              backgroundColor: AuraTheme.background,
            ),
          ),
          child: child!,
        );
      },
    );
    if (picked != null) {
      setState(() {
        _dueDate = picked;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: const Color(0xFF120E25),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(20),
        side: const BorderSide(color: Color(0x1F94A3B8)),
      ),
      child: Container(
        width: 500,
        padding: const EdgeInsets.all(24.0),
        child: Form(
          key: _formKey,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Add New Task',
                  style: Theme.of(context).textTheme.titleLarge?.copyWith(
                        fontWeight: FontWeight.bold,
                      ),
                ),
                const SizedBox(height: 20),

                // Title Input
                TextFormField(
                  controller: _titleController,
                  decoration: const InputDecoration(
                    labelText: 'Task Title',
                    hintText: 'What needs to be done?',
                  ),
                  style: const TextStyle(color: Colors.white),
                  validator: (val) {
                    if (val == null || val.trim().isEmpty) {
                      return 'Please enter a title';
                    }
                    return null;
                  },
                ),
                const SizedBox(height: 16),

                // Description Input
                TextFormField(
                  controller: _descController,
                  decoration: const InputDecoration(
                    labelText: 'Description',
                    hintText: 'Add details (optional)',
                  ),
                  style: const TextStyle(color: Colors.white),
                  maxLines: 3,
                ),
                const SizedBox(height: 20),

                // Category & Priority Row
                Row(
                  children: [
                    // Category Dropdown
                    Expanded(
                      child: DropdownButtonFormField<String>(
                        initialValue: _category,
                        decoration: const InputDecoration(labelText: 'Category'),
                        dropdownColor: const Color(0xFF1E1B4B),
                        items: ['personal', 'academic', 'client', 'project']
                            .map((cat) => DropdownMenuItem(
                                  value: cat,
                                  child: Text(cat.toUpperCase()),
                                ))
                            .toList(),
                        onChanged: (val) {
                          if (val != null) {
                            setState(() => _category = val);
                          }
                        },
                      ),
                    ),
                    const SizedBox(width: 16),
                    // Priority Dropdown
                    Expanded(
                      child: DropdownButtonFormField<String>(
                        initialValue: _priority,
                        decoration: const InputDecoration(labelText: 'Priority'),
                        dropdownColor: const Color(0xFF1E1B4B),
                        items: ['low', 'medium', 'high']
                            .map((prio) => DropdownMenuItem(
                                  value: prio,
                                  child: Text(prio.toUpperCase()),
                                ))
                            .toList(),
                        onChanged: (val) {
                          if (val != null) {
                            setState(() => _priority = val);
                          }
                        },
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 20),

                // Date Picker row
                InkWell(
                  onTap: () => _selectDate(context),
                  borderRadius: BorderRadius.circular(12),
                  child: Container(
                    padding: const EdgeInsets.all(16),
                    decoration: BoxDecoration(
                      color: const Color(0x0DFFFFFF),
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: const Color(0x1F94A3B8)),
                    ),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Row(
                          children: [
                            const Icon(Icons.calendar_today, size: 18, color: AuraTheme.textSecondary),
                            const SizedBox(width: 12),
                            Text(
                              _dueDate == null
                                  ? 'No Due Date Selected'
                                  : DateFormat('MMM d, y').format(_dueDate!),
                              style: TextStyle(
                                color: _dueDate == null
                                    ? AuraTheme.textSecondary
                                    : Colors.white,
                              ),
                            ),
                          ],
                        ),
                        if (_dueDate != null)
                          IconButton(
                            icon: const Icon(Icons.close, size: 16, color: Colors.white54),
                            onPressed: () {
                              setState(() {
                                _dueDate = null;
                              });
                            },
                          ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 32),

                // Cancel/Save Buttons
                Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: [
                    TextButton(
                      onPressed: () => Navigator.pop(context),
                      child: const Text('Cancel', style: TextStyle(color: AuraTheme.textSecondary)),
                    ),
                    const SizedBox(width: 12),
                    ElevatedButton(
                      onPressed: () {
                        if (_formKey.currentState!.validate()) {
                          ref.read(taskRepositoryProvider).addTask(
                                title: _titleController.text.trim(),
                                description: _descController.text.trim(),
                                priority: _priority,
                                dueDate: _dueDate,
                                category: _category,
                              );
                          Navigator.pop(context);
                        }
                      },
                      child: const Text('Save Task'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
