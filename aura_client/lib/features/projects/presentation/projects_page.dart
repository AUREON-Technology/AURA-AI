import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import '../../auth/presentation/auth_provider.dart';
import '../../auth/presentation/sign_out_confirmation.dart';
import '../data/project_providers.dart';
import 'projects_view_model.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';

class ProjectsPage extends ConsumerWidget {
  const ProjectsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final authState = ref.watch(authProvider);
    final projectsAsync = ref.watch(projectsStreamProvider);
    ref.watch(projectCategoryFilterProvider);
    ref.watch(projectsViewModelProvider);
    final vm = ref.read(projectsViewModelProvider.notifier);
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
                          'Projects',
                          style: Theme.of(context).textTheme.displayLarge?.copyWith(
                                fontSize: 28,
                                fontWeight: FontWeight.w900,
                              ),
                        ),
                        Text(
                          'Organize your work into projects',
                          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                color: palette.textSecondary,
                              ),
                        ),
                      ],
                    ),
                    FilledButton.icon(
                      onPressed: () => _showAddProjectDialog(context, ref, vm),
                      icon: const Icon(Icons.add),
                      label: const Text('New Project'),
                    ),
                  ],
                ),
                const SizedBox(height: 24),
                Expanded(
                  child: projectsAsync.when(
                    data: (projects) {
                      if (projects.isEmpty) {
                        return Center(
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Icon(Icons.folder_open, size: 64, color: palette.textSecondary),
                              const SizedBox(height: 16),
                              Text('No projects yet', style: TextStyle(fontSize: 18, color: palette.textSecondary)),
                              const SizedBox(height: 8),
                              Text('Create your first project to get started', style: TextStyle(color: palette.textSecondary)),
                            ],
                          ),
                        );
                      }
                      return ListView.builder(
                        itemCount: projects.length,
                        itemBuilder: (context, index) {
                          final project = projects[index];
                          return _ProjectCard(project: project, onDelete: () => vm.deleteProject(project.id));
                        },
                      );
                    },
                    loading: () => const Center(child: CircularProgressIndicator()),
                    error: (e, st) => Center(
                      child: SingleChildScrollView(
                        child: AuraFailureView(
                          failure: AuraFailure.from(e),
                          onRetry: () => ref.invalidate(projectsStreamProvider),
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

  void _showAddProjectDialog(BuildContext context, WidgetRef ref, ProjectsViewModel vm) {
    final titleController = TextEditingController();
    final descController = TextEditingController();
    String status = 'active';
    DateTime? targetDate;

    showDialog(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setState) => AlertDialog(
          title: const Text('Create Project'),
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
                  items: ['active', 'completed', 'archived', 'on_hold'].map((s) => DropdownMenuItem(value: s, child: Text(s))).toList(),
                  onChanged: (v) => setState(() => status = v!),
                  decoration: const InputDecoration(labelText: 'Status'),
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
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
            FilledButton(
              onPressed: () async {
                if (titleController.text.trim().isEmpty) return;
                await vm.addProject(
                  title: titleController.text.trim(),
                  description: descController.text.trim().isEmpty ? null : descController.text.trim(),
                  status: status,
                  targetDate: targetDate,
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

class _ProjectCard extends StatelessWidget {
  final Project project;
  final VoidCallback onDelete;

  const _ProjectCard({required this.project, required this.onDelete});

  Color _statusColor(AuraPalette palette, String status) {
    switch (status) {
      case 'completed':
        return palette.success;
      case 'archived':
        return palette.textSecondary;
      case 'on_hold':
        return palette.warning;
      default:
        return palette.info;
    }
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: _statusColor(palette, project.status).withValues(alpha: 0.2),
          child: Icon(Icons.folder, color: _statusColor(palette, project.status)),
        ),
        title: Text(project.title, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (project.description != null && project.description!.isNotEmpty)
              Text(project.description!, maxLines: 1, overflow: TextOverflow.ellipsis),
            if (project.targetDate != null)
              Text('Due: ${project.targetDate!.toLocal().toString().split(' ')[0]}', style: TextStyle(fontSize: 12, color: palette.textSecondary)),
          ],
        ),
        trailing: PopupMenuButton<String>(
          onSelected: (value) {
            if (value == 'delete') onDelete();
            // TODO: add edit, duplicate, etc.
          },
          itemBuilder: (context) => [
            const PopupMenuItem(value: 'edit', child: Text('Edit')),
            PopupMenuItem(value: 'delete', child: Text('Delete', style: TextStyle(color: palette.danger))),
          ],
        ),
      ),
    );
  }
}