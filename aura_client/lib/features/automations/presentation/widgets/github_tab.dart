import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import '../../data/automation_repository.dart';
import '../github_view_model.dart';
import 'package:aura_client/l10n/l10n.dart';

class GitHubTab extends ConsumerStatefulWidget {
  const GitHubTab({super.key});

  @override
  ConsumerState<GitHubTab> createState() => _GitHubTabState();
}

class _GitHubTabState extends ConsumerState<GitHubTab> {
  final _patController = TextEditingController();
  final _repoController = TextEditingController(text: 'Anas-Ibrahim/Aura-AI');
  var _appliedSavedPat = false;

  @override
  void dispose() {
    _patController.dispose();
    _repoController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final ui = ref.watch(gitHubViewModelProvider);
    if (!_appliedSavedPat && ui.savedPat != null && ui.savedPat!.isNotEmpty) {
      _patController.text = ui.savedPat!;
      _appliedSavedPat = true;
    }
    final vm = ref.read(gitHubViewModelProvider.notifier);
    final palette = AuraPalette.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(context.l10n.automationsGithubSetup, style: const TextStyle(fontWeight: FontWeight.bold)),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      flex: 3,
                      child: TextField(
                        controller: _patController,
                        obscureText: true,
                        decoration: InputDecoration(
                          labelText: context.l10n.automationsGithubPatLabel,
                          hintText: 'ghp_...',
                        ),
                        style: TextStyle(color: palette.textPrimary),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      flex: 2,
                      child: TextField(
                        controller: _repoController,
                        decoration: InputDecoration(
                          labelText: context.l10n.automationsGithubRepoLabel,
                          hintText: 'owner/repo',
                        ),
                        style: TextStyle(color: palette.textPrimary),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                ElevatedButton.icon(
                  onPressed: () async {
                    final sanitized = sanitizeGitHubRepoPath(_repoController.text);
                    if (sanitized != null) _repoController.text = sanitized;
                    final error = await vm.fetchIssues(token: _patController.text, repoRaw: _repoController.text);
                    if (error != null && context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(
                          content: Text(error),
                          backgroundColor: error.contains('owner/repo') ? palette.warning : palette.danger,
                        ),
                      );
                    }
                  },
                  icon: const Icon(Icons.search),
                  label: Text(context.l10n.automationsGithubLoadIssues),
                  style: ElevatedButton.styleFrom(backgroundColor: palette.brand, foregroundColor: palette.onBrand),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 20),
        Expanded(
          child: ui.issues.isEmpty
              ? Center(
                  child: Text(
                    ui.loading ? context.l10n.automationsGithubLoading : context.l10n.automationsGithubEmpty,
                    style: TextStyle(color: palette.textSecondary),
                  ),
                )
              : ListView.builder(
                  itemCount: ui.issues.length,
                  itemBuilder: (context, index) {
                    final issue = ui.issues[index];
                    final isAnalyzing = ui.analyzingIds.contains(issue.number);
                    final analysis = ui.analyses[issue.number];
                    return _IssueCard(
                      issue: issue,
                      isAnalyzing: isAnalyzing,
                      analysis: analysis,
                      onAnalyze: () async {
                        final error = await vm.analyzeIssue(issue);
                        if (error != null && context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(
                            SnackBar(content: Text(error), backgroundColor: palette.danger),
                          );
                        }
                      },
                    );
                  },
                ),
        ),
      ],
    );
  }
}

class _IssueCard extends ConsumerWidget {
  final GitHubIssueItem issue;
  final bool isAnalyzing;
  final GitHubIssueResult? analysis;
  final VoidCallback onAnalyze;

  const _IssueCard({
    required this.issue,
    required this.isAnalyzing,
    required this.analysis,
    required this.onAnalyze,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    return Card(
      margin: const EdgeInsets.only(bottom: 16),
      child: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    '#${issue.number} - ${issue.title}',
                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
                  ),
                ),
                if (analysis == null)
                  isAnalyzing
                      ? CircularProgressIndicator(color: palette.brand)
                      : OutlinedButton.icon(
                          onPressed: onAnalyze,
                          icon: const Icon(Icons.analytics_outlined, size: 14),
                          label: Text(context.l10n.automationsGithubExtractSubtasks),
                        ),
              ],
            ),
            Divider(color: palette.border, height: 20),
            if (analysis == null)
              Text(
                issue.body.length > 180 ? '${issue.body.substring(0, 180)}...' : issue.body,
                style: TextStyle(color: palette.textSecondary, fontSize: 13),
              )
            else
              GitHubAnalysisPanel(issue: issue, analysis: analysis!),
          ],
        ),
      ),
    );
  }
}

class GitHubAnalysisPanel extends ConsumerWidget {
  final GitHubIssueItem issue;
  final GitHubIssueResult analysis;
  const GitHubAnalysisPanel({super.key, required this.issue, required this.analysis});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final vm = ref.read(gitHubViewModelProvider.notifier);
    final palette = AuraPalette.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: palette.warning.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                context.l10n.automationsGithubComplexity(analysis.complexity),
                style: TextStyle(color: palette.warning, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            ),
            const SizedBox(width: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: palette.success.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                context.l10n.automationsGithubPriority(analysis.suggestedPriority),
                style: TextStyle(color: palette.success, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        Text(context.l10n.automationsGithubIssueSummary, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12)),
        const SizedBox(height: 4),
        Text(analysis.summary, style: TextStyle(color: palette.textSecondary, fontSize: 13)),
        const SizedBox(height: 16),
        if (analysis.subtasks.isNotEmpty) ...[
          Text(context.l10n.automationsGithubCodingTasks, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12)),
          const SizedBox(height: 8),
          ...analysis.subtasks.map((task) {
            return Container(
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: palette.surfaceVariant,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Row(
                children: [
                  Expanded(child: Text(task, style: const TextStyle(fontSize: 13))),
                  IconButton(
                    tooltip: context.l10n.githubImportSubtask,
                    icon: Icon(Icons.add_task, color: palette.success),
                    onPressed: () async {
                      final messenger = ScaffoldMessenger.of(context);
                      final l10n = context.l10n;
                      await vm.importSubtask(issue, analysis, task);
                      messenger.showSnackBar(SnackBar(content: Text(l10n.automationsGithubImported)));
                    },
                  ),
                ],
              ),
            );
          }),
        ],
      ],
    );
  }
}
