import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/theme/theme.dart';
import '../../data/automation_repository.dart';
import '../gmail_view_model.dart';

class GmailTab extends ConsumerWidget {
  final bool isMock;
  const GmailTab({super.key, required this.isMock});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (isMock) {
      return const Card(
        child: Padding(
          padding: EdgeInsets.all(24.0),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(Icons.lock_outline, size: 64, color: AuraTheme.accentAmber),
              SizedBox(height: 16),
              Text(
                'Live Gmail Integration Unavailable',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white),
              ),
              SizedBox(height: 8),
              Text(
                'You are signed in via a mock developer profile. Real unread emails can only be fetched when logged in with a real Google Account.',
                textAlign: TextAlign.center,
                style: TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
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
            const Text(
              'Recent Unread Emails',
              style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white),
            ),
            ui.loading
                ? const SizedBox(
                    width: 24,
                    height: 24,
                    child: CircularProgressIndicator(strokeWidth: 2, color: AuraTheme.accentIndigo),
                  )
                : ElevatedButton.icon(
                    onPressed: () async {
                      final error = await vm.fetchUnread();
                      if (error != null && context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(content: Text(error), backgroundColor: Colors.redAccent),
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
                    style: const TextStyle(color: AuraTheme.textSecondary),
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
                            SnackBar(content: Text(error), backgroundColor: Colors.redAccent),
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
                      Text('From: ${email.sender}', style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 12)),
                    ],
                  ),
                ),
                if (analysis == null)
                  isAnalyzing
                      ? const CircularProgressIndicator(color: AuraTheme.accentIndigo)
                      : OutlinedButton.icon(
                          onPressed: onAnalyze,
                          icon: const Icon(Icons.auto_awesome, size: 14),
                          label: const Text('AI Analyze'),
                        ),
              ],
            ),
            const Divider(color: Color(0x1F94A3B8), height: 24),
            if (analysis == null)
              Text(email.snippet, style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 13))
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

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: AuraTheme.accentIndigo.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                'Category: ${analysis.category}',
                style: const TextStyle(color: AuraTheme.accentIndigo, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            ),
            const SizedBox(width: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: isUrgent ? Colors.redAccent.withValues(alpha: 0.15) : AuraTheme.accentTeal.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                isUrgent ? 'URGENT' : 'Low Urgency',
                style: TextStyle(color: isUrgent ? Colors.redAccent : AuraTheme.accentTeal, fontSize: 11, fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        const Text('AI Summary', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 12, color: Colors.white)),
        const SizedBox(height: 4),
        Text(analysis.summary, style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 13)),
        const SizedBox(height: 16),
        if (analysis.actionItems.isNotEmpty) ...[
          const Text('Action Items Extracted', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 12, color: Colors.white)),
          const SizedBox(height: 8),
          ...analysis.actionItems.map((item) {
            return Container(
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: const Color(0x0AFFFFFF),
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
                            style: const TextStyle(color: AuraTheme.accentAmber, fontSize: 11),
                          ),
                      ],
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.playlist_add, color: AuraTheme.accentTeal),
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
          const Text('Suggested Calendar Event', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 12, color: Colors.white)),
          const SizedBox(height: 8),
          Container(
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(
              color: AuraTheme.accentIndigo.withValues(alpha: 0.1),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: AuraTheme.accentIndigo.withValues(alpha: 0.3)),
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
                        style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 11),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.event, color: AuraTheme.accentIndigo),
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
