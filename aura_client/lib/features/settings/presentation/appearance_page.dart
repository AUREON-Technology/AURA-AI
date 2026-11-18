import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/locale/app_language.dart';
import 'package:aura_client/core/theme/theme_mode_controller.dart';
import 'package:aura_client/l10n/l10n.dart';

class AppearancePage extends ConsumerWidget {
  const AppearancePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final mode = ref.watch(themeModeProvider);
    final language = ref.watch(appLanguageProvider);
    final palette = AuraPalette.of(context);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text(context.l10n.appearanceTitle, style: Theme.of(context).textTheme.headlineMedium),
      ),
      body: ListView(
        padding: const EdgeInsets.all(AuraSpacing.lg),
        children: [
          AuraCard(
            padding: EdgeInsets.zero,
            // ListTile paints its ink on the nearest Material; AuraCard's own fill would hide it.
            child: Material(
              type: MaterialType.transparency,
              child: Column(
                children: [
                  for (final option in ThemeMode.values) ...[
                    if (option != ThemeMode.values.first) const Divider(height: 1),
                    ListTile(
                      title: Text(_label(context, option)),
                      subtitle: Text(_subtitle(context, option)),
                      trailing: Icon(
                        mode == option ? Icons.check_circle : Icons.circle_outlined,
                        color: mode == option ? palette.brand : palette.textDisabled,
                      ),
                      onTap: () => ref.read(themeModeProvider.notifier).setMode(option),
                    ),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: AuraSpacing.lg),
          Padding(
            padding: const EdgeInsetsDirectional.only(start: AuraSpacing.xs, bottom: AuraSpacing.sm),
            child: Text(
              context.l10n.languageSectionTitle,
              style: Theme.of(context).textTheme.titleSmall?.copyWith(color: palette.textSecondary),
            ),
          ),
          AuraCard(
            padding: EdgeInsets.zero,
            child: Material(
              type: MaterialType.transparency,
              child: Column(
                children: [
                  for (final option in AppLanguage.values) ...[
                    if (option != AppLanguage.values.first) const Divider(height: 1),
                    ListTile(
                      key: ValueKey('language-${option.name}'),
                      title: Text(_languageLabel(context, option)),
                      subtitle: option == AppLanguage.system
                          ? Text(context.l10n.languageSystemSubtitle)
                          : null,
                      trailing: Icon(
                        language == option ? Icons.check_circle : Icons.circle_outlined,
                        color: language == option ? palette.brand : palette.textDisabled,
                      ),
                      onTap: () => ref.read(appLanguageProvider.notifier).setLanguage(option),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// Each language is named in its own script, never translated, so someone who cannot read
  /// the current language can still find theirs.
  static const _nativeNames = {AppLanguage.english: 'English', AppLanguage.arabic: 'العربية'};

  String _languageLabel(BuildContext context, AppLanguage language) =>
      _nativeNames[language] ?? context.l10n.languageSystem;

  String _label(BuildContext context, ThemeMode mode) => switch (mode) {
        ThemeMode.system => context.l10n.themeSystem,
        ThemeMode.light => context.l10n.themeLight,
        ThemeMode.dark => context.l10n.themeDark,
      };

  String _subtitle(BuildContext context, ThemeMode mode) => switch (mode) {
        ThemeMode.system => context.l10n.themeSystemSubtitle,
        ThemeMode.light => context.l10n.themeLightSubtitle,
        ThemeMode.dark => context.l10n.themeDarkSubtitle,
      };
}
