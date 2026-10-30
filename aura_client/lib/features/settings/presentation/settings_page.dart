import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/platform/aura_platform.dart';

/// "You" — account, knowledge, memory, feedback, and appearance.
class SettingsPage extends ConsumerWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = AuraPalette.of(context);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text('You', style: Theme.of(context).textTheme.headlineMedium),
      ),
      body: ListView(
        padding: const EdgeInsets.all(AuraSpacing.lg),
        children: [
          _SectionLabel('Account'),
          AuraCard(
            padding: EdgeInsets.zero,
            child: Column(
              children: [
                AuraListTile(
                  icon: Icons.person_outline,
                  title: 'Profile',
                  subtitle: 'Signed-in account and security',
                  onTap: () => context.push('/profile'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.palette_outlined,
                  title: 'Appearance',
                  subtitle: 'System, light, or dark',
                  onTap: () => context.push('/appearance'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.dns_outlined,
                  title: 'Server environment',
                  subtitle: 'Production or development backend',
                  onTap: () => context.push('/server-environment'),
                ),
              ],
            ),
          ),
          if (canRunDesktopIde) ...[
            const SizedBox(height: AuraSpacing.xl),
            _SectionLabel('Desktop'),
            const AuraCard(
              padding: EdgeInsets.zero,
              child: _DesktopIdeToggleTile(),
            ),
          ],
          const SizedBox(height: AuraSpacing.xl),
          _SectionLabel('Your AURA'),
          AuraCard(
            padding: EdgeInsets.zero,
            child: Column(
              children: [
                AuraListTile(
                  icon: Icons.folder_shared_outlined,
                  title: 'Knowledge',
                  subtitle: 'Documents Copilot can search',
                  onTap: () => context.push('/knowledge'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.psychology_outlined,
                  title: 'Memory',
                  subtitle: 'Typed facts AURA remembers',
                  onTap: () => context.push('/memory'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.rate_review_outlined,
                  title: 'Feedback',
                  subtitle: 'Corrections you’ve sent',
                  onTap: () => context.push('/feedback'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.code_outlined,
                  title: 'Engineer',
                  subtitle: 'Workspaces, PRs, and sandbox runs',
                  onTap: () => context.push('/engineer-history'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.hub_outlined,
                  title: 'GitHub Connection',
                  subtitle: 'Connect your own GitHub account',
                  onTap: () => context.push('/github-connect'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.cloud_outlined,
                  iconColor: AuraPalette.of(context).info,
                  title: 'Engine status',
                  subtitle: 'Whether AURA’s server is reachable',
                  onTap: () => context.push('/models'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.bolt_outlined,
                  iconColor: AuraPalette.of(context).warning,
                  title: 'Automations',
                  subtitle: 'Gmail, GitHub, and Drive helpers',
                  onTap: () => context.push('/automations'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.phone_iphone_outlined,
                  iconColor: AuraPalette.of(context).success,
                  title: 'Usage on this device',
                  subtitle: 'Local latency and request counts',
                  onTap: () => context.push('/analytics'),
                ),
              ],
            ),
          ),
          const SizedBox(height: AuraSpacing.xl),
          _SectionLabel('About'),
          const AuraCard(
            child: _AboutCard(),
          ),
        ],
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
      padding: const EdgeInsets.fromLTRB(AuraSpacing.xs, 0, AuraSpacing.xs, AuraSpacing.sm),
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
      title: 'Desktop IDE',
      subtitle: _useIde
          ? 'AURA opens the IDE shell on this machine'
          : 'AURA opens the regular Home screen on this machine',
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
        Text('Where AURA runs', style: Theme.of(context).textTheme.titleSmall),
        const SizedBox(height: AuraSpacing.sm),
        Text(
          'Requests are processed on AURA’s own servers. They are never sent to a '
          'third-party AI service, and never processed on this device.',
          style: Theme.of(context).textTheme.bodySmall,
        ),
      ],
    );
  }
}
