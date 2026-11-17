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
import 'package:aura_client/l10n/l10n.dart';

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
                            context.l10n.projectsWelcomeBack,
                            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                  color: palette.textSecondary,
                                ),
                          ),
                          Text(
                            user?.displayName ?? context.l10n.projectsDefaultUserName,
                            style: Theme.of(context).textTheme.titleLarge?.copyWith(
                                  fontWeight: FontWeight.bold,
                                ),
                          ),
                        ],
                      ),
                    ),
                    IconButton(
                      icon: Icon(Icons.logout, color: palette.danger),
                      tooltip: context.l10n.signOut,
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
                          context.l10n.projectsTitle,
                          style: Theme.of(context).textTheme.displayLarge?.copyWith(
                                fontSize: 28,
                                fontWeight: FontWeight.w900,
                              ),
                        ),
                        Text(
                          context.l10n.projectsSubtitle,
                          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                color: palette.textSecondary,
                              ),
                        ),
                      ],
                    ),
                    FilledButton.icon(
                      onPressed: () => _showAddProjectDialog(context, ref, vm),
                      icon: const Icon(Icons.add),
                      label: Text(context.l10n.projectsNew),
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
                              Text(context.l10n.projectsEmptyTitle, style: TextStyle(fontSize: 18, color: palette.textSecondary)),
                              const SizedBox(height: 8),
                              Text(context.l10n.projectsEmptyMessage, style: TextStyle(color: palette.textSecondary)),
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
          title: Text(context.l10n.projectsCreateTitle),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextField(
                  controller: titleController,
                  decoration: InputDecoration(labelText: context.l10n.projectsTitleField),
                  autofocus: true,
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: descController,
                  decoration: InputDecoration(labelText: context.l10n.projectsDescriptionField),
                  maxLines: 3,
                ),
                const SizedBox(height: 16),
                DropdownButtonFormField<String>(
                  initialValue: status,
                  items: ['active', 'completed', 'archived', 'on_hold'].map((s) => DropdownMenuItem(value: s, child: Text(projectStatusLabel(context.l10n, s)))).toList(),
                  onChanged: (v) => setState(() => status = v!),
                  decoration: InputDecoration(labelText: context.l10n.projectsStatusField),
                ),
                const SizedBox(height: 16),
                ListTile(
                  title: Text(targetDate == null
                      ? context.l10n.projectsSetTargetDate
                      : context.l10n.projectsTarget(targetDate!.toLocal().toString().split(' ')[0])),
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
            TextButton(onPressed: () => Navigator.pop(context), child: Text(context.l10n.cancel)),
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
              child: Text(context.l10n.projectsCreate),
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
              Text(context.l10n.projectsDue(project.targetDate!.toLocal().toString().split(' ')[0]), style: TextStyle(fontSize: 12, color: palette.textSecondary)),
          ],
        ),
        trailing: PopupMenuButton<String>(
          onSelected: (value) {
            if (value == 'delete') onDelete();
            // TODO: add edit, duplicate, etc.
          },
          itemBuilder: (context) => [
            PopupMenuItem(value: 'edit', child: Text(context.l10n.commonEdit)),
            PopupMenuItem(value: 'delete', child: Text(context.l10n.commonDelete, style: TextStyle(color: palette.danger))),
          ],
        ),
      ),
    );
  }
}
/// A project status (a stored identifier such as `on_hold`) in the language on screen.
String projectStatusLabel(AppLocalizations l10n, String status) {
  switch (status) {
    case 'active':
      return l10n.projectsStatusActive;
    case 'completed':
      return l10n.projectsStatusCompleted;
    case 'archived':
      return l10n.projectsStatusArchived;
    case 'on_hold':
      return l10n.projectsStatusOnHold;
    default:
      return status;
  }
}
