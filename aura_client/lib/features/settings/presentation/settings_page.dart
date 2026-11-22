import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/config/backend_url_settings.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/features/auth/presentation/delete_account_confirmation.dart';
import 'package:aura_client/features/engineering/presentation/engineer_consent_tile.dart';
import 'package:aura_client/features/workspaces/presentation/workspace_tile.dart';
import 'package:aura_client/l10n/l10n.dart';

/// Opens one of You's pages. Beside a detail pane (the web layout) it replaces what the pane
/// shows; everywhere else it stacks the page on top, as the mobile app always did.
void _open(BuildContext context, String path) {
  if (AuraPaneScope.maybeOf(context) != null) {
    context.go(path);
  } else {
    context.push(path);
  }
}

bool _isOpen(BuildContext context, String path) =>
    AuraPaneScope.maybeOf(context)?.isSelected(path) ?? false;

/// "You" — grouped as Account, (Desktop,) Your AURA, Engineer, System and
/// About, so each of its pages is found under the heading it belongs to rather
/// than in one long mixed list.
class SettingsPage extends ConsumerWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text(
          l10n.youTitle,
          style: Theme.of(context).textTheme.headlineMedium,
        ),
      ),
      body: Align(
        alignment: Alignment.topCenter,
        child: ConstrainedBox(
          // A settings list stretched across a desktop window is hard to scan;
          // keep it to a readable column.
          constraints: const BoxConstraints(maxWidth: 720),
          child: ListView(
            padding: const EdgeInsets.all(AuraSpacing.lg),
            // Each heading and its card arrive in turn, top to bottom.
            children: [
              for (final (i, item) in <Widget>[
                _SectionLabel(l10n.settingsSectionAccount),
                AuraCard(
                  padding: EdgeInsets.zero,
                  child: Column(
                    children: [
                      const WorkspaceTile(),
                      const TeamChatsTile(),
                      const _RowDivider(),
                      AuraListTile(
                        key: const Key('plan-tile'),
                        icon: Icons.workspace_premium_outlined,
                        title: l10n.planTitle,
                        selected: _isOpen(context, '/plan'),
                        onTap: () => _open(context, '/plan'),
                      ),
                      const _RowDivider(),
                      AuraListTile(
                        icon: Icons.person_outline,
                        title: l10n.settingsProfileTitle,
                        subtitle: l10n.settingsProfileSubtitle,
                        selected: _isOpen(context, '/profile'),
                        onTap: () => _open(context, '/profile'),
                      ),
                      const _RowDivider(),
                      AuraListTile(
                        icon: Icons.palette_outlined,
                        title: l10n.appearanceTitle,
                        subtitle: l10n.settingsAppearanceSubtitle,
                        selected: _isOpen(context, '/appearance'),
                        onTap: () => _open(context, '/appearance'),
                      ),
                      // A release build has one server to offer, so no picker (see
                      // BackendUrlSettings.selectableEnvironments).
                      if (BackendUrlSettings.offersEnvironmentChoice) ...[
                        const _RowDivider(),
                        AuraListTile(
                          icon: Icons.dns_outlined,
                          title: l10n.serverEnvironmentTitle,
                          subtitle: l10n.settingsServerEnvironmentSubtitle,
                          selected: _isOpen(context, '/server-environment'),
                        onTap: () => _open(context, '/server-environment'),
                        ),
                      ],
                      const _RowDivider(),
                      AuraListTile(
                        icon: Icons.devices_outlined,
                        title: l10n.devicesTitle,
                        subtitle: l10n.settingsDevicesSubtitle,
                        selected: _isOpen(context, '/desktop-devices'),
                        onTap: () => _open(context, '/desktop-devices'),
                      ),
                      const _RowDivider(),
                      AuraListTile(
                        key: const Key('sign-in-methods-tile'),
                        icon: Icons.key_outlined,
                        title: l10n.signInMethodsTitle,
                        subtitle: l10n.signInMethodsSubtitle,
                        selected: _isOpen(context, '/sign-in-methods'),
                        onTap: () => _open(context, '/sign-in-methods'),
                      ),
                      const _RowDivider(),
                      const DeleteAccountTile(),
                    ],
                  ),
                ),
                const SizedBox(height: AuraSpacing.xl),
                _SectionLabel(l10n.settingsSectionYourAura),
                AuraCard(
                  padding: EdgeInsets.zero,
                  child: Column(
                    children: [
                      AuraListTile(
                        icon: Icons.folder_shared_outlined,
                        title: l10n.knowledgeTitle,
                        subtitle: l10n.settingsKnowledgeSubtitle,
                        selected: _isOpen(context, '/knowledge'),
                        onTap: () => _open(context, '/knowledge'),
                      ),
                      const _RowDivider(),
                      AuraListTile(
                        icon: Icons.psychology_outlined,
                        title: l10n.memoryTitle,
                        subtitle: l10n.settingsMemorySubtitle,
                        selected: _isOpen(context, '/memory'),
                        onTap: () => _open(context, '/memory'),
                      ),
                      const _RowDivider(),
                      AuraListTile(
                        icon: Icons.bolt_outlined,
                        iconColor: AuraPalette.of(context).warning,
                        title: l10n.settingsAutomationsTitle,
                        subtitle: l10n.settingsAutomationsSubtitle,
                        selected: _isOpen(context, '/automations'),
                        onTap: () => _open(context, '/automations'),
                      ),
                      const _RowDivider(),
                      AuraListTile(
                        icon: Icons.rate_review_outlined,
                        title: l10n.feedbackTitle,
                        subtitle: l10n.settingsFeedbackSubtitle,
                        selected: _isOpen(context, '/feedback'),
                        onTap: () => _open(context, '/feedback'),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: AuraSpacing.xl),
                _SectionLabel(l10n.settingsSectionEngineer),
                AuraCard(
                  padding: EdgeInsets.zero,
                  child: Column(
                    children: [
                      AuraListTile(
                        icon: Icons.code_outlined,
                        title: l10n.settingsEngineerTitle,
                        subtitle: l10n.settingsEngineerSubtitle,
                        selected: _isOpen(context, '/engineer-history'),
                        onTap: () => _open(context, '/engineer-history'),
                      ),
                      const _RowDivider(),
                      AuraListTile(
                        icon: Icons.hub_outlined,
                        title: l10n.settingsGithubTitle,
                        subtitle: l10n.settingsGithubSubtitle,
                        selected: _isOpen(context, '/github-connect'),
                        onTap: () => _open(context, '/github-connect'),
                      ),
                      const _RowDivider(),
                      const EngineerConsentTile(),
                    ],
                  ),
                ),
                const SizedBox(height: AuraSpacing.xl),
                _SectionLabel(l10n.settingsSectionSystem),
                AuraCard(
                  padding: EdgeInsets.zero,
                  child: Column(
                    children: [
                      AuraListTile(
                        icon: Icons.cloud_outlined,
                        iconColor: AuraPalette.of(context).info,
                        title: l10n.engineStatusTitle,
                        subtitle: l10n.settingsEngineStatusSubtitle,
                        selected: _isOpen(context, '/models'),
                        onTap: () => _open(context, '/models'),
                      ),
                      const _RowDivider(),
                      AuraListTile(
                        icon: Icons.phone_iphone_outlined,
                        iconColor: AuraPalette.of(context).success,
                        title: l10n.settingsUsageTitle,
                        subtitle: l10n.settingsUsageSubtitle,
                        selected: _isOpen(context, '/analytics'),
                        onTap: () => _open(context, '/analytics'),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: AuraSpacing.xl),
                _SectionLabel(l10n.settingsSectionAbout),
                const AuraCard(child: _AboutCard()),
              ].indexed)
                AuraEntrance(
                  delay: AuraEntrance.stagger(
                    i,
                    step: const Duration(milliseconds: 30),
                  ),
                  child: item,
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _SectionLabel extends StatelessWidget {
  final String text;
  const _SectionLabel(this.text);

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AuraSpacing.xs,
        0,
        AuraSpacing.xs,
        AuraSpacing.sm,
      ),
      child: Text(
        text.toUpperCase(),
        style: TextStyle(
          color: palette.textDisabled,
          fontSize: 11,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.6,
        ),
      ),
    );
  }
}

class _RowDivider extends StatelessWidget {
  const _RowDivider();
  @override
  Widget build(BuildContext context) =>
      Divider(height: 1, indent: 56, color: AuraPalette.of(context).border);
}

class _AboutCard extends StatelessWidget {
  const _AboutCard();

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          context.l10n.settingsAboutTitle,
          style: Theme.of(context).textTheme.titleSmall,
        ),
        const SizedBox(height: AuraSpacing.sm),
        Text(
          context.l10n.settingsAboutBody,
          style: Theme.of(context).textTheme.bodySmall,
        ),
      ],
    );
  }
}
