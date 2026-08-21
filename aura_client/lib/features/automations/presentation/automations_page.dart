import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_provider.dart';
import 'package:aura_client/core/theme/theme.dart';
import 'automations_view_model.dart';
import 'widgets/drive_tab.dart';
import 'widgets/gmail_tab.dart';
import 'widgets/github_tab.dart';

class AutomationsPage extends ConsumerWidget {
  const AutomationsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authProvider).value;
    final isMock = auth?.id == 'mock_user_123' || auth == null;
    final activeTab = ref.watch(automationsViewModelProvider);

    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment(0.6, 0.6),
            radius: 1.5,
            colors: [
              Color(0xFF1E1B4B),
              Color(0xFF0F0C1B),
            ],
          ),
        ),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24.0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  'AURA Automations',
                  style: Theme.of(context).textTheme.displayLarge?.copyWith(fontSize: 28),
                ),
                const SizedBox(height: 4),
                const Text(
                  'Extract tasks from emails and issues, generate workspace structures',
                  style: TextStyle(color: AuraTheme.textSecondary, fontSize: 14),
                ),
                const SizedBox(height: 20),
                const _AutomationsTabs(),
                const SizedBox(height: 24),
                Expanded(
                  child: activeTab == 0
                      ? GmailTab(isMock: isMock)
                      : activeTab == 1
                          ? const GitHubTab()
                          : DriveTab(isMock: isMock),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _AutomationsTabs extends ConsumerWidget {
  const _AutomationsTabs();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final activeTab = ref.watch(automationsViewModelProvider);
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          _TabItem(index: 0, label: 'Gmail Inbox Analyzer', icon: Icons.mail_outline, isActive: activeTab == 0),
          const SizedBox(width: 12),
          _TabItem(index: 1, label: 'GitHub Issue Importer', icon: Icons.code, isActive: activeTab == 1),
          const SizedBox(width: 12),
          _TabItem(index: 2, label: 'Drive Workspace Builder', icon: Icons.cloud_queue, isActive: activeTab == 2),
        ],
      ),
    );
  }
}

class _TabItem extends ConsumerWidget {
  final int index;
  final String label;
  final IconData icon;
  final bool isActive;

  const _TabItem({
    required this.index,
    required this.label,
    required this.icon,
    required this.isActive,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return GestureDetector(
      onTap: () => ref.read(automationsViewModelProvider.notifier).selectTab(index),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: isActive ? AuraTheme.accentIndigo.withValues(alpha: 0.15) : AuraTheme.cardBg,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: isActive ? AuraTheme.accentIndigo : const Color(0x1F94A3B8),
            width: 1,
          ),
        ),
        child: Row(
          children: [
            Icon(icon, color: isActive ? AuraTheme.accentIndigo : AuraTheme.textSecondary, size: 16),
            const SizedBox(width: 8),
            Text(
              label,
              style: TextStyle(
                color: isActive ? Colors.white : AuraTheme.textSecondary,
                fontWeight: isActive ? FontWeight.bold : FontWeight.normal,
                fontSize: 13,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
