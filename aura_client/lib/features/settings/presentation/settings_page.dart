import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:aura_client/core/design_system/design_system.dart';

/// V2 rebuild (Doc/V2_BLUEPRINT.md §3) — replaces the V1 flat grid of 5
/// equally-weighted dashboard cards (which implied Profile/Models/
/// Learning/Knowledge/Analytics/Automations are opened equally often, when
/// in reality most are occasional/power-user screens) with a single
/// grouped list, the iOS/Android Settings-app pattern. "You" in the IA.
class SettingsPage extends ConsumerWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Theme(
      data: AuraThemeV2.dark,
      child: Builder(builder: (context) => _SettingsScaffold(ref: ref)),
    );
  }
}

class _SettingsScaffold extends StatelessWidget {
  final WidgetRef ref;
  const _SettingsScaffold({required this.ref});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text('Settings', style: Theme.of(context).textTheme.headlineMedium),
      ),
      body: ListView(
        padding: const EdgeInsets.all(AuraSpacing.lg),
        children: [
          _SectionLabel('Dashboards & tools'),
          AuraCard(
            padding: EdgeInsets.zero,
            child: Column(
              children: [
                _SettingsListItem(
                  icon: Icons.person_outline,
                  color: palette.brandOnSurfaceText,
                  title: 'Profile',
                  subtitle: 'Auth sessions, attestation logs, security checks',
                  onTap: () => context.go('/profile'),
                ),
                const _RowDivider(),
                _SettingsListItem(
                  icon: Icons.cloud_outlined,
                  color: palette.info,
                  title: 'AI engine status',
                  subtitle: 'Read-only: whether AURA server inference is reachable',
                  onTap: () => context.go('/models'),
                ),
                const _RowDivider(),
                _SettingsListItem(
                  icon: Icons.psychology_outlined,
                  color: palette.warning,
                  title: 'Continuous learning',
                  subtitle: 'Export Alpaca/ChatML feedback logs for LoRA fine-tuning',
                  onTap: () => context.go('/learning'),
                ),
                const _RowDivider(),
                _SettingsListItem(
                  icon: Icons.folder_shared_outlined,
                  color: palette.brandOnSurfaceText,
                  title: 'Knowledge center',
                  subtitle: 'Ingest text documents and search local vector stores',
                  onTap: () => context.go('/knowledge'),
                ),
                const _RowDivider(),
                _SettingsListItem(
                  icon: Icons.bar_chart_outlined,
                  color: palette.success,
                  title: 'System analytics',
                  subtitle: 'Latency trends, server-sse vs failover, request volume',
                  onTap: () => context.go('/analytics'),
                ),
                const _RowDivider(),
                _SettingsListItem(
                  icon: Icons.bolt_outlined,
                  color: palette.warning,
                  title: 'Automations',
                  subtitle: 'AI-assisted Gmail, GitHub, and Drive workflows',
                  onTap: () => context.go('/automations'),
                ),
              ],
            ),
          ),
          const SizedBox(height: AuraSpacing.xl),
          _SectionLabel('AI processing'),
          const _AIConfigCard(),
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
        style: TextStyle(color: palette.textDisabled, fontSize: 11, fontWeight: FontWeight.w700, letterSpacing: 0.6),
      ),
    );
  }
}

class _RowDivider extends StatelessWidget {
  const _RowDivider();
  @override
  Widget build(BuildContext context) => Divider(height: 1, indent: 56, color: AuraPalette.of(context).border);
}

class _SettingsListItem extends StatelessWidget {
  final IconData icon;
  final Color color;
  final String title;
  final String subtitle;
  final VoidCallback onTap;

  const _SettingsListItem({
    required this.icon,
    required this.color,
    required this.title,
    required this.subtitle,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Semantics(
      button: true,
      label: '$title. $subtitle',
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.lg, vertical: AuraSpacing.md),
          child: Row(
            children: [
              CircleAvatar(backgroundColor: color.withValues(alpha: 0.12), radius: 18, child: Icon(icon, color: color, size: 18)),
              const SizedBox(width: AuraSpacing.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: Theme.of(context).textTheme.titleSmall),
                    const SizedBox(height: 2),
                    Text(subtitle, style: Theme.of(context).textTheme.bodySmall, maxLines: 1, overflow: TextOverflow.ellipsis),
                  ],
                ),
              ),
              Icon(Icons.chevron_right, color: palette.textDisabled),
            ],
          ),
        ),
      ),
    );
  }
}

class _AIConfigCard extends StatelessWidget {
  /// Reports where inference happens. Nothing here is configurable, deliberately.
  ///
  /// This card used to offer a provider dropdown (auto / Gemini / three on-device models) and a
  /// text field for an Ollama daemon address. Every one of those options is gone: inference runs on
  /// AURA's server, the client no longer holds a hosted-API key, and the client is not supposed to
  /// know the model server's address -- that field handed it to anyone who opened Settings.
  ///
  /// Replaced with a statement rather than deleted outright, so the section a user knows still
  /// exists and answers the question the controls were really being used to ask: where does my data
  /// go? An empty section, or a dropdown with one item, would answer it worse.
  const _AIConfigCard();

  @override
  Widget build(BuildContext context) {
    return AuraCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Where AURA runs', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: AuraSpacing.sm),
          Text(
            'Requests are processed on AURA\'s own servers. They are never sent to a third-party '
            'AI service, and never processed on this device.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: AuraSpacing.sm),
          Text(
            'Model selection and updates are managed centrally — there is nothing to configure here.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}
