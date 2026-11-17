import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import '../../data/automation_repository.dart';
import '../gmail_view_model.dart';

class GmailTab extends ConsumerWidget {
  final bool isMock;
  const GmailTab({super.key, required this.isMock});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    if (isMock) {
      return Card(
        child: Padding(
          padding: const EdgeInsets.all(24.0),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.lock_outline, size: 64, color: palette.warning),
              const SizedBox(height: 16),
              Text(
                'Live Gmail Integration Unavailable',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: palette.textPrimary),
              ),
              const SizedBox(height: 8),
              Text(
                'You are signed in via a mock developer profile. Real unread emails can only be fetched when logged in with a real Google Account.',
                textAlign: TextAlign.center,
                style: TextStyle(color: palette.textSecondary, fontSize: 13),
              ),
            ],
          ),
        ),
      );
    }

    final ui = ref.watch(gmailViewModelProvider);
    final vm = ref.read(gmailViewModelProvider.notifier);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              'Recent Unread Emails',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: palette.textPrimary),
            ),
            ui.loading
                ? SizedBox(
                    width: 24,
                    height: 24,
                    child: CircularProgressIndicator(strokeWidth: 2, color: palette.brand),
                  )
                : ElevatedButton.icon(
                    onPressed: () async {
                      final error = await vm.fetchUnread();
                      if (error != null && context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(content: Text(error), backgroundColor: palette.danger),
                        );
                      }
                    },
                    icon: const Icon(Icons.refresh),
                    label: const Text('Fetch Unread'),
                  ),
          ],
        ),
        const SizedBox(height: 16),
        Expanded(
          child: ui.emails.isEmpty
              ? Center(
                  child: Text(
                    ui.loading ? 'Fetching...' : 'No unread emails loaded. Click Fetch.',
                    style: TextStyle(color: palette.textSecondary),
                  ),
                )
              : ListView.builder(
                  itemCount: ui.emails.length,
                  itemBuilder: (context, index) {
                    final email = ui.emails[index];
                    final isAnalyzing = ui.analyzingIds.contains(email.id);
                    final analysis = ui.analyses[email.id];
                    return _EmailCard(
                      email: email,
                      isAnalyzing: isAnalyzing,
                      analysis: analysis,
                      onAnalyze: () async {
                        final error = await vm.analyzeEmail(email);
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

class _EmailCard extends ConsumerWidget {
  final GmailMessageHeader email;
  final bool isAnalyzing;
  final EmailAnalysisResult? analysis;
  final VoidCallback onAnalyze;

  const _EmailCard({
    required this.email,
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
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(email.subject, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
                      const SizedBox(height: 4),
                      Text('From: ${email.sender}', style: TextStyle(color: palette.textSecondary, fontSize: 12)),
                    ],
                  ),
                ),
                if (analysis == null)
                  isAnalyzing
                      ? CircularProgressIndicator(color: palette.brand)
                      : OutlinedButton.icon(
                          onPressed: onAnalyze,
                          icon: const Icon(Icons.auto_awesome, size: 14),
                          label: const Text('AI Analyze'),
                        ),
              ],
            ),
            Divider(color: palette.border, height: 24),
            if (analysis == null)
              Text(email.snippet, style: TextStyle(color: palette.textSecondary, fontSize: 13))
            else
              EmailAnalysisPanel(analysis: analysis!),
          ],
        ),
      ),
    );
  }
}

class EmailAnalysisPanel extends ConsumerWidget {
  final EmailAnalysisResult analysis;
  const EmailAnalysisPanel({super.key, required this.analysis});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final isUrgent = analysis.urgency.toLowerCase() == 'high';
    final vm = ref.read(gmailViewModelProvider.notifier);
    final palette = AuraPalette.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: palette.brand.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                'Category: ${analysis.category}',
                style: TextStyle(color: palette.brandOnSurfaceText, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            ),
            const SizedBox(width: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: isUrgent ? palette.danger.withValues(alpha: 0.15) : palette.success.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                isUrgent ? 'URGENT' : 'Low Urgency',
                style: TextStyle(color: isUrgent ? palette.danger : palette.success, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        Text('AI Summary', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 12, color: palette.textPrimary)),
        const SizedBox(height: 4),
        Text(analysis.summary, style: TextStyle(color: palette.textSecondary, fontSize: 13)),
        const SizedBox(height: 16),
        if (analysis.actionItems.isNotEmpty) ...[
          Text('Action Items Extracted', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 12, color: palette.textPrimary)),
          const SizedBox(height: 8),
          ...analysis.actionItems.map((item) {
            return Container(
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: palette.surfaceVariant,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(item.title, style: const TextStyle(fontSize: 13)),
                        if (item.dueDate != null)
                          Text(
                            'Suggested Deadline: ${DateFormat('yyyy-MM-dd').format(item.dueDate!)}',
                            style: TextStyle(color: palette.warning, fontSize: 11),
                          ),
                      ],
                    ),
                  ),
                  IconButton(
                    icon: Icon(Icons.playlist_add, color: palette.success),
                    tooltip: 'Import as AURA Task',
                    onPressed: () async {
                      final messenger = ScaffoldMessenger.of(context);
                      await vm.importActionItem(item, urgent: isUrgent, category: analysis.category);
                      messenger.showSnackBar(SnackBar(content: Text('Imported task: ${item.title}')));
                    },
                  ),
                ],
              ),
            );
          }),
        ],
        if (analysis.shouldCreateEvent && analysis.eventTitle != null && analysis.eventStart != null) ...[
          const SizedBox(height: 12),
          Text('Suggested Calendar Event', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 12, color: palette.textPrimary)),
          const SizedBox(height: 8),
          Container(
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(
              color: palette.brand.withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: palette.brand.withValues(alpha: 0.3)),
            ),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(analysis.eventTitle!, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.bold)),
                      const SizedBox(height: 2),
                      Text(
                        '${DateFormat('yyyy-MM-dd HH:mm').format(analysis.eventStart!)} - ${DateFormat('HH:mm').format(analysis.eventEnd ?? analysis.eventStart!.add(const Duration(hours: 1)))}',
                        style: TextStyle(color: palette.textSecondary, fontSize: 11),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  icon: Icon(Icons.event, color: palette.brandOnSurfaceText),
                  tooltip: 'Schedule Event',
                  onPressed: () async {
                    final messenger = ScaffoldMessenger.of(context);
                    await vm.importSuggestedEvent(analysis);
                    messenger.showSnackBar(SnackBar(content: Text('Scheduled event: ${analysis.eventTitle}')));
                  },
                ),
              ],
            ),
          ),
        ],
      ],
    );
  }
}
