import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:aura_client/core/config/backend_url_settings.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/platform/aura_platform.dart';
import 'package:aura_client/features/auth/presentation/delete_account_confirmation.dart';
import 'package:aura_client/l10n/l10n.dart';

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
            children: [
              _SectionLabel(l10n.settingsSectionAccount),
              AuraCard(
                padding: EdgeInsets.zero,
                child: Column(
                  children: [
                    AuraListTile(
                      icon: Icons.person_outline,
                      title: l10n.settingsProfileTitle,
                      subtitle: l10n.settingsProfileSubtitle,
                      onTap: () => context.push('/profile'),
                    ),
                    const _RowDivider(),
                    AuraListTile(
                      icon: Icons.palette_outlined,
                      title: l10n.appearanceTitle,
                      subtitle: l10n.settingsAppearanceSubtitle,
                      onTap: () => context.push('/appearance'),
                    ),
                    // A release build has one server to offer, so no picker (see
                    // BackendUrlSettings.selectableEnvironments).
                    if (BackendUrlSettings.offersEnvironmentChoice) ...[
                      const _RowDivider(),
                      AuraListTile(
                        icon: Icons.dns_outlined,
                        title: l10n.serverEnvironmentTitle,
                        subtitle: l10n.settingsServerEnvironmentSubtitle,
                        onTap: () => context.push('/server-environment'),
                      ),
                    ],
                    const _RowDivider(),
                    AuraListTile(
                      icon: Icons.devices_outlined,
                      title: l10n.devicesTitle,
                      subtitle: l10n.settingsDevicesSubtitle,
                      onTap: () => context.push('/desktop-devices'),
                    ),
                    const _RowDivider(),
                    const DeleteAccountTile(),
                  ],
                ),
              ),
              if (canRunDesktopIde) ...[
                const SizedBox(height: AuraSpacing.xl),
                _SectionLabel(l10n.settingsSectionDesktop),
                const AuraCard(
                  padding: EdgeInsets.zero,
                  child: _DesktopIdeToggleTile(),
                ),
              ],
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
                      onTap: () => context.push('/knowledge'),
                    ),
                    const _RowDivider(),
                    AuraListTile(
                      icon: Icons.psychology_outlined,
                      title: l10n.memoryTitle,
                      subtitle: l10n.settingsMemorySubtitle,
                      onTap: () => context.push('/memory'),
                    ),
                    const _RowDivider(),
                    AuraListTile(
                      icon: Icons.bolt_outlined,
                      iconColor: AuraPalette.of(context).warning,
                      title: l10n.settingsAutomationsTitle,
                      subtitle: l10n.settingsAutomationsSubtitle,
                      onTap: () => context.push('/automations'),
                    ),
                    const _RowDivider(),
                    AuraListTile(
                      icon: Icons.rate_review_outlined,
                      title: l10n.feedbackTitle,
                      subtitle: l10n.settingsFeedbackSubtitle,
                      onTap: () => context.push('/feedback'),
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
                      onTap: () => context.push('/engineer-history'),
                    ),
                    const _RowDivider(),
                    AuraListTile(
                      icon: Icons.hub_outlined,
                      title: l10n.settingsGithubTitle,
                      subtitle: l10n.settingsGithubSubtitle,
                      onTap: () => context.push('/github-connect'),
                    ),
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
                      onTap: () => context.push('/models'),
                    ),
                    const _RowDivider(),
                    AuraListTile(
                      icon: Icons.phone_iphone_outlined,
                      iconColor: AuraPalette.of(context).success,
                      title: l10n.settingsUsageTitle,
                      subtitle: l10n.settingsUsageSubtitle,
                      onTap: () => context.push('/analytics'),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: AuraSpacing.xl),
              _SectionLabel(l10n.settingsSectionAbout),
              const AuraCard(child: _AboutCard()),
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

/// Toggles the `desktop_use_ide` SharedPreferences flag the router checks
/// (`core/routing/router.dart`) to decide whether a Windows user lands on
/// AURA Home (`/today`) or the Desktop IDE (`/ide`) after signing in — never
/// a forced redirect, per ADR-015. Without this control the IDE built under
/// `desktop/` was unreachable: nothing else in the app ever wrote that key.
class _DesktopIdeToggleTile extends StatefulWidget {
  const _DesktopIdeToggleTile();

  @override
  State<_DesktopIdeToggleTile> createState() => _DesktopIdeToggleTileState();
}

class _DesktopIdeToggleTileState extends State<_DesktopIdeToggleTile> {
  bool _loading = true;
  bool _useIde = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _useIde = prefs.getBool('desktop_use_ide') ?? false;
      _loading = false;
    });
  }

  Future<void> _setUseIde(bool value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('desktop_use_ide', value);
    if (!mounted) return;
    setState(() => _useIde = value);
    if (value) {
      // Open the IDE now. The preference itself only picks where AURA lands
      // after sign-in; it no longer traps `/today` (audit F-34), and the IDE
      // palette's "Leave IDE" command comes back here.
      GoRouter.of(context).go('/ide');
    }
  }

  @override
  Widget build(BuildContext context) {
    return AuraListTile(
      icon: Icons.dashboard_customize_outlined,
      title: context.l10n.settingsDesktopIdeTitle,
      subtitle: _useIde
          ? context.l10n.settingsDesktopIdeOn
          : context.l10n.settingsDesktopIdeOff,
      trailing: _loading
          ? const SizedBox(
              width: 20,
              height: 20,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : Switch(value: _useIde, onChanged: _setUseIde),
    );
  }
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
