import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';

class EmptyStateChat extends StatelessWidget {
  final Function(String) onSuggestionSelected;
  const EmptyStateChat({super.key, required this.onSuggestionSelected});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: SingleChildScrollView(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const AuraEmptyState(
              icon: Icons.auto_awesome,
              title: 'Ask Copilot Anything',
              message: 'Programming questions, academic inquiries, or coordinating your tasks.',
            ),
            const SizedBox(height: AuraSpacing.lg),
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 500),
              child: GridView.count(
                shrinkWrap: true,
                crossAxisCount: MediaQuery.of(context).size.width < 500 ? 1 : 2,
                crossAxisSpacing: AuraSpacing.md,
                mainAxisSpacing: AuraSpacing.md,
                childAspectRatio: MediaQuery.of(context).size.width < 500 ? 3.8 : 2.2,
                physics: const NeverScrollableScrollPhysics(),
                children: [
                  _SuggestionCard(
                    title: 'Explain a Concept',
                    subtitle: 'Ask a programming or academic question',
                    prompt: 'Explain how dependency injection works',
                    onSelected: onSuggestionSelected,
                  ),
                  _SuggestionCard(
                    title: 'Log Health',
                    subtitle: 'Log water intake and exercise duration for today',
                    prompt: 'Log 500ml of water and 30 minutes of exercise',
                    onSelected: onSuggestionSelected,
                  ),
                  _SuggestionCard(
                    title: 'Financial Health',
                    subtitle: 'Show my current balance and expense distribution',
                    prompt: 'How are my expenses this month?',
                    onSelected: onSuggestionSelected,
                  ),
                  _SuggestionCard(
                    title: 'Agent Memory',
                    subtitle: 'View facts the system has remembered about me',
                    prompt: 'What do you remember about me?',
                    onSelected: onSuggestionSelected,
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SuggestionCard extends StatelessWidget {
  final String title;
  final String subtitle;
  final String prompt;
  final Function(String) onSelected;

  const _SuggestionCard({
    required this.title,
    required this.subtitle,
    required this.prompt,
    required this.onSelected,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return AuraCard(
      padding: const EdgeInsets.all(14.0),
      onTap: () => onSelected(prompt),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(title, style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: palette.textPrimary)),
          const SizedBox(height: AuraSpacing.xs),
          Text(subtitle, maxLines: 2, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 11, color: palette.textSecondary)),
        ],
      ),
    );
  }
}
