/// Activity bar for Desktop IDE.
///
/// Far-left vertical bar with icons for Explorer, Search, Source Control, etc.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

class ActivityBar extends ConsumerWidget {
  const ActivityBar({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ideDensity = context.ideDensity;
    final palette = AuraPalette.of(context);
    final layoutState = ref.watch(layoutNotifierProvider);

    return Container(
      width: ideDensity.activityBarWidth,
      color: palette.surface,
      child: Column(
        children: [
          // App icon / logo
          Padding(
            padding: EdgeInsets.all(ideDensity.spacingScale * AuraSpacing.md),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(AuraRadius.sm),
              child: Image.asset(
                'assets/app_icon.png',
                width: 28,
                height: 28,
                fit: BoxFit.contain,
              ),
            ),
          ),
          const Spacer(),
          // Activity items
          _ActivityItem(
            icon: Icons.folder_outlined,
            selectedIcon: Icons.folder,
            label: 'Explorer',
            isSelected: layoutState.showPrimarySidebar,
            onTap: () => ref.read(layoutNotifierProvider.notifier).togglePrimarySidebar(),
            density: ideDensity,
          ),
          _ActivityItem(
            icon: Icons.search_outlined,
            selectedIcon: Icons.search,
            label: 'Search',
            isSelected: false,
            onTap: () {}, // D2 stub
            density: ideDensity,
          ),
          _ActivityItem(
            icon: Icons.source_outlined,
            selectedIcon: Icons.source,
            label: 'Source Control',
            isSelected: false,
            onTap: () {}, // D5 stub
            density: ideDensity,
          ),
          _ActivityItem(
            icon: Icons.smart_toy_outlined,
            selectedIcon: Icons.smart_toy,
            label: 'AURA Agents',
            isSelected: layoutState.showSecondarySidebar,
            onTap: () => ref.read(layoutNotifierProvider.notifier).toggleSecondarySidebar(),
            density: ideDensity,
          ),
          _ActivityItem(
            icon: Icons.auto_awesome_outlined,
            selectedIcon: Icons.auto_awesome,
            label: 'AURA',
            isSelected: false,
            onTap: () {}, // D6 stub
            density: ideDensity,
          ),
          const Spacer(),
          // Settings at bottom
          _ActivityItem(
            icon: Icons.settings_outlined,
            selectedIcon: Icons.settings,
            label: 'Settings',
            isSelected: false,
            onTap: () => ref.read(layoutNotifierProvider.notifier).toggleBottomPanel(),
            density: ideDensity,
          ),
          SizedBox(height: ideDensity.spacingScale * AuraSpacing.md),
        ],
      ),
    );
  }
}

class _ActivityItem extends StatelessWidget {
  const _ActivityItem({
    required this.icon,
    required this.selectedIcon,
    required this.label,
    required this.isSelected,
    required this.onTap,
    required this.density,
  });

  final IconData icon;
  final IconData selectedIcon;
  final String label;
  final bool isSelected;
  final VoidCallback onTap;
  final AuraIdeDensity density;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Semantics(
      button: true,
      label: label,
      child: InkWell(
        onTap: onTap,
        child: Container(
          width: density.activityBarWidth,
          height: density.activityBarWidth,
          margin: EdgeInsets.symmetric(
            vertical: density.spacingScale * AuraSpacing.xs,
            horizontal: density.spacingScale * AuraSpacing.xs,
          ),
          decoration: BoxDecoration(
            color: isSelected ? palette.brand.withValues(alpha: 0.1) : Colors.transparent,
            borderRadius: BorderRadius.circular(AuraRadius.sm),
          ),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(
                isSelected ? selectedIcon : icon,
                size: 20,
                color: isSelected ? palette.brand : palette.textSecondary,
              ),
              if (isSelected)
                Container(
                  margin: const EdgeInsets.only(top: 2),
                  width: 6,
                  height: 6,
                  decoration: BoxDecoration(
                    color: palette.brand,
                    shape: BoxShape.circle,
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}