import 'package:flutter/material.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Copilot's first screen: what this is, and four ways to start.
///
/// It is the screen a new user sees most, so it carries the brand mark rather
/// than the neutral grey icon [AuraEmptyState] uses for "nothing here yet" --
/// an empty conversation is an invitation, not an absence.
class EmptyStateChat extends StatelessWidget {
  final Function(String) onSuggestionSelected;
  const EmptyStateChat({super.key, required this.onSuggestionSelected});

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final palette = AuraPalette.of(context);
    final textTheme = Theme.of(context).textTheme;
    final narrow = MediaQuery.of(context).size.width < 500;

    final suggestions = [
      (
        Icons.lightbulb_outline,
        l10n.chatSuggestExplainTitle,
        l10n.chatSuggestExplainSubtitle,
        l10n.chatSuggestExplainPrompt,
      ),
      (
        Icons.favorite_outline,
        l10n.chatSuggestHealthTitle,
        l10n.chatSuggestHealthSubtitle,
        l10n.chatSuggestHealthPrompt,
      ),
      (
        Icons.account_balance_wallet_outlined,
        l10n.chatSuggestFinanceTitle,
        l10n.chatSuggestFinanceSubtitle,
        l10n.chatSuggestFinancePrompt,
      ),
      (
        Icons.psychology_outlined,
        l10n.chatSuggestMemoryTitle,
        l10n.chatSuggestMemorySubtitle,
        l10n.chatSuggestMemoryPrompt,
      ),
    ];

    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.symmetric(
          horizontal: AuraSpacing.lg,
          vertical: AuraSpacing.xl,
        ),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 560),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              ExcludeSemantics(
                child: Container(
                  width: 64,
                  height: 64,
                  decoration: BoxDecoration(
                    gradient: palette.brandGradient,
                    borderRadius: BorderRadius.circular(AuraRadius.lg + 4),
                    boxShadow: [
                      BoxShadow(
                        color: palette.brand.withValues(alpha: 0.35),
                        blurRadius: 28,
                        offset: const Offset(0, 10),
                      ),
                    ],
                  ),
                  child: Icon(
                    Icons.auto_awesome,
                    color: palette.onBrand,
                    size: 30,
                  ),
                ),
              ),
              const SizedBox(height: AuraSpacing.xl),
              Semantics(
                header: true,
                child: Text(
                  l10n.chatEmptyTitle,
                  textAlign: TextAlign.center,
                  style: textTheme.headlineSmall?.copyWith(
                    fontWeight: FontWeight.w700,
                    color: palette.textPrimary,
                  ),
                ),
              ),
              const SizedBox(height: AuraSpacing.sm),
              Text(
                l10n.chatEmptyMessage,
                textAlign: TextAlign.center,
                style: textTheme.bodyMedium?.copyWith(
                  color: palette.textSecondary,
                ),
              ),
              const SizedBox(height: AuraSpacing.xxl),
              GridView.count(
                shrinkWrap: true,
                crossAxisCount: narrow ? 1 : 2,
                crossAxisSpacing: AuraSpacing.md,
                mainAxisSpacing: AuraSpacing.md,
                childAspectRatio: narrow ? 3.4 : 2.2,
                physics: const NeverScrollableScrollPhysics(),
                children: [
                  for (final s in suggestions)
                    _SuggestionCard(
                      icon: s.$1,
                      title: s.$2,
                      subtitle: s.$3,
                      prompt: s.$4,
                      onSelected: onSuggestionSelected,
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _SuggestionCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final String prompt;
  final Function(String) onSelected;

  const _SuggestionCard({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.prompt,
    required this.onSelected,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return AuraCard(
      padding: const EdgeInsets.all(AuraSpacing.md + 2),
      onTap: () => onSelected(prompt),
      child: Row(
        children: [
          Container(
            width: 36,
            height: 36,
            decoration: BoxDecoration(
              color: palette.brand.withValues(alpha: 0.14),
              borderRadius: BorderRadius.circular(AuraRadius.sm + 2),
            ),
            child: Icon(icon, size: 18, color: palette.brandOnSurfaceText),
          ),
          const SizedBox(width: AuraSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Text(
                  title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontWeight: FontWeight.w600,
                    fontSize: 13,
                    color: palette.textPrimary,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  subtitle,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 11.5,
                    color: palette.textSecondary,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
