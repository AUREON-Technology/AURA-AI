import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

class EmptyStateChat extends StatelessWidget {
  final Function(String) onSuggestionSelected;
  const EmptyStateChat({super.key, required this.onSuggestionSelected});

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return Center(
      child: SingleChildScrollView(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            AuraEmptyState(
              icon: Icons.auto_awesome,
              title: l10n.chatEmptyTitle,
              message: l10n.chatEmptyMessage,
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
                    title: l10n.chatSuggestExplainTitle,
                    subtitle: l10n.chatSuggestExplainSubtitle,
                    prompt: l10n.chatSuggestExplainPrompt,
                    onSelected: onSuggestionSelected,
                  ),
                  _SuggestionCard(
                    title: l10n.chatSuggestHealthTitle,
                    subtitle: l10n.chatSuggestHealthSubtitle,
                    prompt: l10n.chatSuggestHealthPrompt,
                    onSelected: onSuggestionSelected,
                  ),
                  _SuggestionCard(
                    title: l10n.chatSuggestFinanceTitle,
                    subtitle: l10n.chatSuggestFinanceSubtitle,
                    prompt: l10n.chatSuggestFinancePrompt,
                    onSelected: onSuggestionSelected,
                  ),
                  _SuggestionCard(
                    title: l10n.chatSuggestMemoryTitle,
                    subtitle: l10n.chatSuggestMemorySubtitle,
                    prompt: l10n.chatSuggestMemoryPrompt,
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
