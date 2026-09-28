/// Width classes for the Desktop IDE shell.
///
/// The shell used to lay out the activity bar, primary sidebar, editor,
/// secondary sidebar and bottom panel at their persisted pixel widths at
/// every window size, with no width branch anywhere. At a narrow window that
/// leaves the editor whatever remains after ~650px of chrome, and nothing
/// collapses (UX_BASELINE_AUDIT.md F-06).
library;

import 'dart:math' as math;

import 'package:flutter/foundation.dart';

/// How much room the shell has, in the only terms the layout cares about.
///
/// The thresholds are where a panel stops paying for the width it takes, not
/// device categories — a desktop window dragged narrow is [compact] and should
/// behave like one.
enum DesktopBreakpoint {
  /// Editor only. Both sidebars open over the content rather than beside it.
  compact,

  /// Explorer beside the editor; the AI panel opens over the content, because
  /// three columns at this width leaves the editor too narrow to work in.
  standard,

  /// Explorer, editor and AI panel all side by side.
  wide;

  /// Below this the primary sidebar stops being docked.
  static const double compactMaxWidth = 900;

  /// Below this the secondary (AI) sidebar stops being docked.
  static const double standardMaxWidth = 1280;

  static DesktopBreakpoint of(double width) {
    if (width < compactMaxWidth) return DesktopBreakpoint.compact;
    if (width < standardMaxWidth) return DesktopBreakpoint.standard;
    return DesktopBreakpoint.wide;
  }

  /// The Explorer / Source Control / Control Center sidebar floats over the
  /// editor instead of taking a column from it.
  bool get primarySidebarIsOverlay => this == DesktopBreakpoint.compact;

  /// The AURA agent sidebar floats. True for everything but [wide].
  bool get secondarySidebarIsOverlay => this != DesktopBreakpoint.wide;

  /// Minimum width the editor keeps no matter what a persisted panel width
  /// says. A docked panel is clamped so the thing being edited never becomes
  /// the smallest region on screen.
  static const double minEditorWidth = 420;

  /// The width a docked panel may actually take, given [available] (the shell
  /// width less the activity bar and any other docked panel).
  ///
  /// [preferred] is the user's own persisted width and is honoured whenever
  /// it fits; the clamp only ever takes width away from a panel that would
  /// squeeze the editor below [minEditorWidth], and never below
  /// [minPanelWidth], since a 40px Explorer helps nobody either.
  static const double minPanelWidth = 180;

  static double dockedPanelWidth(double preferred, double available) {
    final ceiling = math.max(minPanelWidth, available - minEditorWidth);
    return math.min(preferred, ceiling);
  }
}


/// Which shell regions are docked, which float, and how wide each one is —
/// resolved from the breakpoint and the user's persisted widths.
///
/// This is a value, not a widget, so the layout rules can be asserted at every
/// width without standing up the Explorer, the editor and the agent panel (and
/// the daemon polling behind them) to look at the result.
@immutable
class ShellRegions {
  const ShellRegions({
    required this.primaryDocked,
    required this.primaryFloating,
    required this.secondaryDocked,
    required this.secondaryFloating,
    required this.primaryWidth,
    required this.secondaryWidth,
    required this.secondaryFillsContentArea,
  });

  /// Explorer / Source Control / Control Center takes its own column.
  final bool primaryDocked;

  /// ...or floats over the editor.
  final bool primaryFloating;

  /// The AURA agent panel takes its own column.
  final bool secondaryDocked;

  /// ...or floats over the editor.
  final bool secondaryFloating;

  /// Width for the primary sidebar, docked or floating. `0` when it is hidden.
  final double primaryWidth;

  /// Width for the secondary sidebar, docked or floating. `0` when it is
  /// hidden, and meaningless when [secondaryFillsContentArea] is true.
  final double secondaryWidth;

  /// The floating agent panel takes the whole content area rather than a strip
  /// of it. True only in [DesktopBreakpoint.compact]: a chat column 200px wide
  /// is not something anyone can work in.
  final bool secondaryFillsContentArea;

  /// True when something is covering the editor, and therefore when a scrim
  /// and an Escape binding are needed.
  bool get hasFloatingPanel => primaryFloating || secondaryFloating;

  static ShellRegions resolve({
    required DesktopBreakpoint breakpoint,
    required bool showPrimarySidebar,
    required bool showSecondarySidebar,
    required double preferredPrimaryWidth,
    required double preferredSecondaryWidth,
    required double availableWidth,
    required double activityBarWidth,
  }) {
    final primaryDocked = showPrimarySidebar && !breakpoint.primarySidebarIsOverlay;
    final primaryFloating = showPrimarySidebar && breakpoint.primarySidebarIsOverlay;
    final secondaryDocked = showSecondarySidebar && !breakpoint.secondarySidebarIsOverlay;
    final secondaryFloating = showSecondarySidebar && breakpoint.secondarySidebarIsOverlay;

    final contentWidth = math.max(0.0, availableWidth - activityBarWidth);

    // The secondary panel is resolved first so the primary is clamped against
    // what is actually left, not against the full content area -- otherwise
    // two docked panels can each individually "fit" and together squeeze the
    // editor below its minimum.
    final secondaryWidth = showSecondarySidebar
        ? DesktopBreakpoint.dockedPanelWidth(preferredSecondaryWidth, contentWidth)
        : 0.0;
    final remainingForPrimary = secondaryDocked ? contentWidth - secondaryWidth : contentWidth;
    final primaryWidth = showPrimarySidebar
        ? DesktopBreakpoint.dockedPanelWidth(preferredPrimaryWidth, remainingForPrimary)
        : 0.0;

    return ShellRegions(
      primaryDocked: primaryDocked,
      primaryFloating: primaryFloating,
      secondaryDocked: secondaryDocked,
      secondaryFloating: secondaryFloating,
      primaryWidth: primaryWidth,
      secondaryWidth: secondaryWidth,
      secondaryFillsContentArea:
          secondaryFloating && breakpoint == DesktopBreakpoint.compact,
    );
  }
}
