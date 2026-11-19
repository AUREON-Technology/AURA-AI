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
import 'package:aura_client/l10n/l10n.dart';

class ActivityBar extends ConsumerWidget {
  const ActivityBar({
    super.key,
    this.agentPanelFloating = false,
    this.sidebarFloating = false,
  });

  /// The AI panel is floating over the content area (narrow windows), so a
  /// primary sidebar opened underneath it would not be visible.
  final bool agentPanelFloating;

  /// The primary sidebar is floating over the content area.
  final bool sidebarFloating;

  /// Opens [panel] in the primary sidebar, or closes it if it is already the
  /// one showing.
  ///
  /// A floating AI panel is drawn over the primary sidebar, so opening a
  /// primary panel underneath it would change nothing the user can see.
  /// Instead the click closes the AI panel and makes sure [panel] is the one
  /// revealed: one click reaches the destination that was asked for.
  void _openPrimary(
    LayoutNotifier notifier,
    LayoutState layoutState,
    PrimarySidebarPanel panel,
    void Function() show,
  ) {
    if (agentPanelFloating) {
      notifier.toggleSecondarySidebar();
      show();
      return;
    }
    if (layoutState.showPrimarySidebar && layoutState.primarySidebarPanel == panel) {
      notifier.togglePrimarySidebar();
    } else {
      show();
    }
  }

  /// Toggles the bottom panel -- or, while something floats over the content
  /// area, closes what floats and shows the bottom panel, which would
  /// otherwise open behind the scrim.
  void _openBottomPanel(LayoutNotifier notifier, LayoutState layoutState) {
    if (!agentPanelFloating && !sidebarFloating) {
      notifier.toggleBottomPanel();
      return;
    }
    if (agentPanelFloating) notifier.toggleSecondarySidebar();
    if (sidebarFloating) notifier.togglePrimarySidebar();
    if (!layoutState.showBottomPanel) notifier.toggleBottomPanel();
  }

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
          // Activity items sit right under the logo, as in every IDE: they used to be centred
          // between two Spacers, leaving a third of the bar empty above them.
          _ActivityItem(
            icon: Icons.folder_outlined,
            selectedIcon: Icons.folder,
            label: context.l10n.ideActivityExplorer,
            tooltip: context.l10n.ideActivityExplorerTooltip,
            isSelected: layoutState.showPrimarySidebar &&
                layoutState.primarySidebarPanel == PrimarySidebarPanel.explorer,
            onTap: () {
              final notifier = ref.read(layoutNotifierProvider.notifier);
              _openPrimary(notifier, layoutState, PrimarySidebarPanel.explorer, notifier.showExplorerPanel);
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
            label: context.l10n.ideActivityGoToFile,
            tooltip: context.l10n.ideActivityGoToFileTooltip,
            isSelected: false,
            onTap: hasWorkspace
                ? () => ref.read(ideEventBusProvider).publish(const ShowQuickOpenRequested())
                : null,
            density: ideDensity,
          ),
          _ActivityItem(
            icon: Icons.source_outlined,
            selectedIcon: Icons.source,
            label: context.l10n.ideActivitySourceControl,
            tooltip: context.l10n.ideActivitySourceControl,
            isSelected: layoutState.showPrimarySidebar &&
                layoutState.primarySidebarPanel == PrimarySidebarPanel.sourceControl,
            onTap: () {
              final notifier = ref.read(layoutNotifierProvider.notifier);
              _openPrimary(
                notifier,
                layoutState,
                PrimarySidebarPanel.sourceControl,
                notifier.showSourceControlPanel,
              );
            },
            density: ideDensity,
          ),
          _ActivityItem(
            icon: Icons.smart_toy_outlined,
            selectedIcon: Icons.smart_toy,
            label: context.l10n.ideActivityAgents,
            tooltip: context.l10n.ideActivityAgentsTooltip,
            isSelected: layoutState.showSecondarySidebar,
            onTap: () => ref.read(layoutNotifierProvider.notifier).toggleSecondarySidebar(),
            density: ideDensity,
          ),
          _ActivityItem(
            icon: Icons.auto_awesome_outlined,
            selectedIcon: Icons.auto_awesome,
            label: 'AURA',
            tooltip: context.l10n.ideActivityControlCenterTooltip,
            isSelected: layoutState.showPrimarySidebar &&
                layoutState.primarySidebarPanel == PrimarySidebarPanel.controlCenter,
            onTap: () {
              final notifier = ref.read(layoutNotifierProvider.notifier);
              _openPrimary(
                notifier,
                layoutState,
                PrimarySidebarPanel.controlCenter,
                notifier.showControlCenterPanel,
              );
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
            label: context.l10n.ideActivityPanel,
            tooltip: context.l10n.ideActivityPanelTooltip,
            badgeCount: problemCount,
            badgeSemanticLabel: (count) => context.l10n.ideActivityPanelProblems(count),
            isSelected: layoutState.showBottomPanel,
            onTap: () => _openBottomPanel(ref.read(layoutNotifierProvider.notifier), layoutState),
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
    this.badgeSemanticLabel,
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

  /// The whole semantic label for a non-zero [badgeCount], e.g. "Panel, 3
  /// problems" (localized, so the plural is the language's own). A bare
  /// number read aloud says nothing about what it counts, and a coloured dot
  /// says nothing at all.
  final String Function(int count)? badgeSemanticLabel;

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
    final semanticLabel = badgeCount > 0 && badgeSemanticLabel != null
        ? badgeSemanticLabel!(badgeCount)
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
