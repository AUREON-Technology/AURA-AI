import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/theme/theme_mode_controller.dart';
import 'package:aura_client/l10n/l10n.dart';

class AppearancePage extends ConsumerWidget {
  const AppearancePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final mode = ref.watch(themeModeProvider);
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
        ],
      ),
    );
  }

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
