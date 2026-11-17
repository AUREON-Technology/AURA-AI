import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../auth/presentation/auth_provider.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';
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
    final palette = AuraPalette.of(context);

    return Scaffold(
      backgroundColor: palette.background,
      body: Container(
        decoration: BoxDecoration(
          gradient: RadialGradient(
            center: const Alignment(0.6, 0.6),
            radius: 1.5,
            colors: [
              palette.brand.withValues(alpha: 0.12),
              Colors.transparent,
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
                  context.l10n.automationsTitle,
                  style: Theme.of(context).textTheme.displayLarge?.copyWith(fontSize: 28),
                ),
                const SizedBox(height: 4),
                Text(
                  context.l10n.automationsSubtitle,
                  style: TextStyle(color: palette.textSecondary, fontSize: 14),
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
          _TabItem(index: 0, label: context.l10n.automationsTabGmail, icon: Icons.mail_outline, isActive: activeTab == 0),
          const SizedBox(width: 12),
          _TabItem(index: 1, label: context.l10n.automationsTabGithub, icon: Icons.code, isActive: activeTab == 1),
          const SizedBox(width: 12),
          _TabItem(index: 2, label: context.l10n.automationsTabDrive, icon: Icons.cloud_queue, isActive: activeTab == 2),
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
    final palette = AuraPalette.of(context);
    return GestureDetector(
      onTap: () => ref.read(automationsViewModelProvider.notifier).selectTab(index),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: isActive ? palette.brand.withValues(alpha: 0.15) : palette.surfaceVariant,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: isActive ? palette.brand : palette.border,
            width: 1,
          ),
        ),
        child: Row(
          children: [
            Icon(icon, color: isActive ? palette.brandOnSurfaceText : palette.textSecondary, size: 16),
            const SizedBox(width: 8),
            Text(
              label,
              style: TextStyle(
                color: isActive ? palette.textPrimary : palette.textSecondary,
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
