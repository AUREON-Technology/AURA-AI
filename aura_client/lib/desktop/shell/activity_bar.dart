/// Activity bar for Desktop IDE.
///
/// Far-left vertical bar: Explorer, Go to File, Source Control, AURA Agents,
/// AURA Control Center, and the bottom Panel toggle.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/desktop/diagnostics/diagnostics_notifier.dart';
import 'package:aura_client/desktop/events/ide_event.dart';
import 'package:aura_client/desktop/events/ide_event_bus.dart';
import 'package:aura_client/desktop/state/desktop_application_state.dart';
import 'package:aura_client/desktop/state/state_notifiers.dart';

class ActivityBar extends ConsumerWidget {
  const ActivityBar({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ideDensity = context.ideDensity;
    final palette = AuraPalette.of(context);
    final layoutState = ref.watch(layoutNotifierProvider);
    // Quick Open searches the *workspace*, so with no workspace open there is
    // nothing for it to search. DesktopShell's handler already returns early in
    // that case -- disabling the control says so instead of swallowing the tap.
    final hasWorkspace = ref.watch(workspaceNotifierProvider).activeWorkspace != null;
    // The bottom panel is collapsed by default (LayoutState.showBottomPanel),
    // and the Problems count lives on that panel's own tab strip -- so until
    // now a new diagnostic was invisible in exactly the state the IDE starts
    // in. This is the only always-visible surface it has.
    final problemCount = ref.watch(diagnosticsNotifierProvider.select((s) => s.totalCount));

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
            tooltip: 'Explorer (Ctrl+B)',
            isSelected: layoutState.showPrimarySidebar &&
                layoutState.primarySidebarPanel == PrimarySidebarPanel.explorer,
            onTap: () {
              final notifier = ref.read(layoutNotifierProvider.notifier);
              if (layoutState.showPrimarySidebar && layoutState.primarySidebarPanel == PrimarySidebarPanel.explorer) {
                notifier.togglePrimarySidebar();
              } else {
                notifier.showExplorerPanel();
              }
            },
            density: ideDensity,
          ),
          // Was an enabled icon wired to `onTap: () {}`. A control that renders
          // as actionable and does nothing teaches the user the app is
          // unreliable, so this now drives the Quick Open flow that already
          // exists on the event bus -- and is named for what it actually does
          // (find a file by name), not for the workspace-wide content search
          // that does not exist yet.
          _ActivityItem(
            icon: Icons.search_outlined,
            selectedIcon: Icons.search,
            label: 'Go to File',
            tooltip: 'Go to File (Ctrl+P)',
            isSelected: false,
            onTap: hasWorkspace
                ? () => ref.read(ideEventBusProvider).publish(const ShowQuickOpenRequested())
                : null,
            density: ideDensity,
          ),
          _ActivityItem(
            icon: Icons.source_outlined,
            selectedIcon: Icons.source,
            label: 'Source Control',
            tooltip: 'Source Control',
            isSelected: layoutState.showPrimarySidebar &&
                layoutState.primarySidebarPanel == PrimarySidebarPanel.sourceControl,
            onTap: () {
              final notifier = ref.read(layoutNotifierProvider.notifier);
              if (layoutState.showPrimarySidebar &&
                  layoutState.primarySidebarPanel == PrimarySidebarPanel.sourceControl) {
                notifier.togglePrimarySidebar();
              } else {
                notifier.showSourceControlPanel();
              }
            },
            density: ideDensity,
          ),
          _ActivityItem(
            icon: Icons.smart_toy_outlined,
            selectedIcon: Icons.smart_toy,
            label: 'AURA Agents',
            tooltip: 'AURA Agents (Ctrl+J)',
            isSelected: layoutState.showSecondarySidebar,
            onTap: () => ref.read(layoutNotifierProvider.notifier).toggleSecondarySidebar(),
            density: ideDensity,
          ),
          _ActivityItem(
            icon: Icons.auto_awesome_outlined,
            selectedIcon: Icons.auto_awesome,
            label: 'AURA',
            tooltip: 'AURA Control Center',
            isSelected: layoutState.showPrimarySidebar &&
                layoutState.primarySidebarPanel == PrimarySidebarPanel.controlCenter,
            onTap: () {
              final notifier = ref.read(layoutNotifierProvider.notifier);
              if (layoutState.showPrimarySidebar &&
                  layoutState.primarySidebarPanel == PrimarySidebarPanel.controlCenter) {
                notifier.togglePrimarySidebar();
              } else {
                notifier.showControlCenterPanel();
              }
            },
            density: ideDensity,
          ),
          const Spacer(),
          // This has always called `toggleBottomPanel()`. It was labelled
          // "Settings" with a gear, which taught the wrong model of where
          // settings live and left the panel itself undiscoverable. Labelled
          // for its behaviour; a real Settings entry is tracked separately
          // (UX_BASELINE_AUDIT.md F-02) rather than faked here.
          _ActivityItem(
            icon: Icons.terminal_outlined,
            selectedIcon: Icons.terminal,
            label: 'Panel',
            tooltip: 'Terminal, Output & Problems (Ctrl+Shift+J)',
            badgeCount: problemCount,
            badgeNoun: 'problem',
            isSelected: layoutState.showBottomPanel,
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
    this.tooltip,
    this.badgeCount = 0,
    this.badgeNoun,
  });

  final IconData icon;
  final IconData selectedIcon;
  final String label;
  final bool isSelected;

  /// `null` renders the item disabled. The bar had an item wired to an empty
  /// callback instead, which looks identical to an enabled one.
  final VoidCallback? onTap;
  final AuraIdeDensity density;

  /// The bar is icon-only, so without this the label exists for screen
  /// readers but not for sighted users hovering an unfamiliar glyph.
  final String? tooltip;

  /// Count of things needing attention behind this item. `0` renders nothing.
  final int badgeCount;

  /// Singular noun for [badgeCount] in the semantic label, e.g. `'problem'`
  /// gives "Panel, 3 problems". A bare number read aloud says nothing about
  /// what it counts, and a coloured dot says nothing at all.
  final String? badgeNoun;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final isEnabled = onTap != null;
    final iconColor = !isEnabled
        ? palette.textDisabled
        : isSelected
            ? palette.brand
            : palette.textSecondary;

    final badgeText = badgeCount > 99 ? '99+' : '$badgeCount';
    final semanticLabel = badgeCount > 0 && badgeNoun != null
        ? '$label, $badgeCount $badgeNoun${badgeCount == 1 ? '' : 's'}'
        : label;

    return Semantics(
      button: true,
      label: semanticLabel,
      // Selection is otherwise conveyed by a tint and a 6px dot -- i.e. by
      // colour and a shape a screen reader never sees.
      selected: isSelected,
      enabled: isEnabled,
      child: Tooltip(
        message: tooltip ?? label,
        waitDuration: AuraMotion.emphasized,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(AuraRadius.sm),
          // Keyboard focus needs to be visible, not just present: without a
          // focus colour a Tab-only user cannot tell which item is targeted.
          focusColor: palette.brand.withValues(alpha: 0.24),
          hoverColor: palette.brand.withValues(alpha: 0.06),
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
                Stack(
                  clipBehavior: Clip.none,
                  children: [
                    Icon(
                      isSelected ? selectedIcon : icon,
                      size: 20,
                      color: iconColor,
                    ),
                    if (badgeCount > 0)
                      PositionedDirectional(
                        top: -4,
                        end: -8,
                        // The count itself is the signal, not the colour --
                        // a badge that is only a coloured dot tells a
                        // colour-blind user nothing it doesn't tell everyone.
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                          constraints: const BoxConstraints(minWidth: 14),
                          decoration: BoxDecoration(
                            color: palette.danger,
                            borderRadius: BorderRadius.circular(AuraRadius.pill),
                          ),
                          child: Text(
                            badgeText,
                            textAlign: TextAlign.center,
                            style: TextStyle(
                              fontSize: 9,
                              height: 1.2,
                              fontWeight: FontWeight.w700,
                              color: palette.onBrand,
                            ),
                          ),
                        ),
                      ),
                  ],
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
      ),
    );
  }
}
