import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/theme/theme.dart';
import '../../../core/ai/offline_agents/offline_agent_manager.dart';
import '../../../core/ai/online_agents/online_agent_manager.dart';
import '../../../core/ai/ai_router/aura_ai_router.dart';

class SettingsPage extends ConsumerWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ollamaHost = ref.watch(ollamaHostProvider);
    final geminiKey = ref.watch(localApiKeyProvider);
    final aiProvider = ref.watch(aiProviderSettingProvider);

    return Scaffold(
      backgroundColor: AuraTheme.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: const Text(
          "Settings & Dashboards",
          style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white),
        ),
      ),
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                "System Panels & Dashboards",
                style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      color: Colors.white,
                      fontWeight: FontWeight.bold,
                    ),
              ),
              const SizedBox(height: 12),
              _buildDashboardsGrid(context),
              const SizedBox(height: 28),
              Text(
                "AI Routing & API Keys",
                style: Theme.of(context).textTheme.titleMedium?.copyWith(
                      color: Colors.white,
                      fontWeight: FontWeight.bold,
                    ),
              ),
              const SizedBox(height: 12),
              _buildAIConfigSection(ref, ollamaHost, geminiKey, aiProvider),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildDashboardsGrid(BuildContext context) {
    return GridView.count(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      crossAxisCount: MediaQuery.of(context).size.width < 600 ? 1 : 2,
      crossAxisSpacing: 16,
      mainAxisSpacing: 16,
      childAspectRatio: 2.2,
      children: [
        _buildMenuCard(
          context,
          "User Profile",
          "Manage auth sessions, attestation logs, and security checks.",
          Icons.person_outline,
          AuraTheme.accentIndigo,
          () => context.go('/profile'),
        ),
        _buildMenuCard(
          context,
          "Offline Models",
          "Download, verify, and activate GGUF and Ollama weights.",
          Icons.download_for_offline_outlined,
          AuraTheme.accentTeal,
          () => context.go('/models'),
        ),
        _buildMenuCard(
          context,
          "Continuous Learning",
          "Export Alpaca/ChatML feedback logs for LoRA fine-tuning.",
          Icons.psychology_outlined,
          AuraTheme.accentAmber,
          () => context.go('/learning'),
        ),
        _buildMenuCard(
          context,
          "Knowledge Center",
          "Ingest text documents and search local vector stores.",
          Icons.folder_shared_outlined,
          Colors.blueAccent,
          () => context.go('/knowledge'),
        ),
        _buildMenuCard(
          context,
          "System Analytics",
          "View latency trends, Gemini failovers, and model distributions.",
          Icons.bar_chart_outlined,
          Colors.pinkAccent,
          () => context.go('/analytics'),
        ),
      ],
    );
  }

  Widget _buildMenuCard(
    BuildContext context,
    String title,
    String description,
    IconData icon,
    Color color,
    VoidCallback onTap,
  ) {
    return Card(
      color: AuraTheme.cardBg,
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(16.0),
          child: Row(
            children: [
              CircleAvatar(
                backgroundColor: color.withOpacity(0.1),
                radius: 22,
                child: Icon(icon, color: color, size: 22),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Text(
                      title,
                      style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15, color: Colors.white),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      description,
                      style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 11),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
              const Icon(Icons.chevron_right, color: Colors.white24),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildAIConfigSection(
    WidgetRef ref,
    String host,
    String geminiKey,
    AIProvider activeProvider,
  ) {
    final hostController = TextEditingController(text: host);
    final keyController = TextEditingController(text: geminiKey);

    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              "Model Routing Parameters",
              style: TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: Colors.white),
            ),
            const SizedBox(height: 16),
            DropdownButtonFormField<AIProvider>(
              dropdownColor: AuraTheme.background,
              value: activeProvider,
              items: const [
                DropdownMenuItem(value: AIProvider.auto, child: Text("Auto Failover (Recommended)")),
                DropdownMenuItem(value: AIProvider.gemini, child: Text("Force Gemini 1.5")),
                DropdownMenuItem(value: AIProvider.offlineFast, child: Text("Offline Fast (Qwen/Gemma)")),
                DropdownMenuItem(value: AIProvider.offlineReasoning, child: Text("Offline Reasoning (Phi)")),
                DropdownMenuItem(value: AIProvider.offlinePremium, child: Text("Offline Premium (LLaMA)")),
              ],
              onChanged: (val) {
                if (val != null) {
                  ref.read(aiProviderSettingProvider.notifier).setProvider(val);
                }
              },
              decoration: const InputDecoration(
                labelText: "Active Model Provider",
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: hostController,
              onSubmitted: (val) {
                ref.read(ollamaHostProvider.notifier).setHost(val);
              },
              decoration: const InputDecoration(
                labelText: "Ollama Host Daemon Endpoint",
                hintText: "http://localhost:11434",
                border: OutlineInputBorder(),
              ),
              style: const TextStyle(color: Colors.white, fontSize: 14),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: keyController,
              obscureText: true,
              onSubmitted: (val) {
                ref.read(localApiKeyProvider.notifier).setKey(val);
              },
              decoration: const InputDecoration(
                labelText: "Local Gemini API Key Override",
                hintText: "Enter API Key",
                border: OutlineInputBorder(),
              ),
              style: const TextStyle(color: Colors.white, fontSize: 14),
            ),
            const SizedBox(height: 12),
            const Text(
              "Press Enter on fields to save changes.",
              style: TextStyle(color: AuraTheme.textSecondary, fontSize: 11, fontStyle: FontStyle.italic),
            )
          ],
        ),
      ),
    );
  }
}
