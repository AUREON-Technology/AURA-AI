import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'package:aura_client/core/design_system/design_system.dart';

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
                  onTap: () => context.go('/profile'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.palette_outlined,
                  title: 'Appearance',
                  subtitle: 'System, light, or dark',
                  onTap: () => context.go('/appearance'),
                ),
              ],
            ),
          ),
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
                  onTap: () => context.go('/knowledge'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.psychology_outlined,
                  title: 'Memory',
                  subtitle: 'Typed facts AURA remembers',
                  onTap: () => context.go('/memory'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.rate_review_outlined,
                  title: 'Feedback',
                  subtitle: 'Corrections you’ve sent',
                  onTap: () => context.go('/feedback'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.cloud_outlined,
                  iconColor: AuraPalette.of(context).info,
                  title: 'Engine status',
                  subtitle: 'Whether AURA’s server is reachable',
                  onTap: () => context.go('/models'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.bolt_outlined,
                  iconColor: AuraPalette.of(context).warning,
                  title: 'Automations',
                  subtitle: 'Gmail, GitHub, and Drive helpers',
                  onTap: () => context.go('/automations'),
                ),
                const _RowDivider(),
                AuraListTile(
                  icon: Icons.phone_iphone_outlined,
                  iconColor: AuraPalette.of(context).success,
                  title: 'Usage on this device',
                  subtitle: 'Local latency and request counts',
                  onTap: () => context.go('/analytics'),
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
