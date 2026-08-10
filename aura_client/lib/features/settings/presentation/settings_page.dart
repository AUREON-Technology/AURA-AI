import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/design_system/design_system.dart';
import '../../../core/ai/offline_agents/offline_agent_manager.dart';
import '../../../core/ai/ai_router/aura_ai_router.dart';

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
    final ollamaHost = ref.watch(ollamaHostProvider);
    final aiProvider = ref.watch(aiProviderSettingProvider);

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
                  icon: Icons.download_for_offline_outlined,
                  color: palette.info,
                  title: 'Offline models',
                  subtitle: 'Download, verify, and activate GGUF weights',
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
                  subtitle: 'Latency trends, Gemini failovers, model distribution',
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
          _SectionLabel('AI routing & API keys'),
          _AIConfigCard(ref: ref, host: ollamaHost, activeProvider: aiProvider),
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
  final WidgetRef ref;
  final String host;
  final AIProvider activeProvider;

  const _AIConfigCard({required this.ref, required this.host, required this.activeProvider});

  @override
  Widget build(BuildContext context) {
    final hostController = TextEditingController(text: host);

    return AuraCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Model routing', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: AuraSpacing.md),
          DropdownButtonFormField<AIProvider>(
            initialValue: activeProvider,
            items: const [
              DropdownMenuItem(value: AIProvider.auto, child: Text('Auto failover (recommended)')),
              DropdownMenuItem(value: AIProvider.gemini, child: Text('Force Gemini 2.5')),
              DropdownMenuItem(value: AIProvider.offlineFast, child: Text('Offline fast (Qwen/Gemma)')),
              DropdownMenuItem(value: AIProvider.offlineReasoning, child: Text('Offline reasoning (Phi)')),
              DropdownMenuItem(value: AIProvider.offlinePremium, child: Text('Offline premium (LLaMA)')),
            ],
            onChanged: (val) {
              if (val != null) ref.read(aiProviderSettingProvider.notifier).setProvider(val);
            },
            decoration: const InputDecoration(labelText: 'Active model provider'),
          ),
          const SizedBox(height: AuraSpacing.md),
          TextField(
            controller: hostController,
            onSubmitted: (val) => ref.read(ollamaHostProvider.notifier).setHost(val),
            decoration: const InputDecoration(labelText: 'Ollama host daemon endpoint', hintText: 'http://localhost:11434'),
          ),
          const SizedBox(height: AuraSpacing.sm),
          Text('Press Enter on a field to save.', style: Theme.of(context).textTheme.bodySmall),
        ],
      ),
    );
  }
}
