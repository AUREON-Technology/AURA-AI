/// UX_BASELINE_AUDIT.md F-06: the Desktop shell laid out the activity bar,
/// both sidebars, the editor and the bottom panel at their persisted pixel
/// widths at every window size — there was no width branch anywhere in
/// desktop_shell.dart. At a narrow window the editor got whatever remained
/// after ~650px of chrome and nothing collapsed.
///
/// The layout rules now resolve to a [ShellRegions] value, which is what
/// these assert: standing up the Explorer, the editor and the agent panel
/// (and the daemon polling behind them) just to measure a column would test
/// far more than the rule.
library;

import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/desktop/shell/desktop_breakpoint.dart';

/// The real default (AuraIdeDensity.defaultDensity), kept as a literal so a
/// density change shows up here as a deliberate edit rather than silently
/// moving every expectation below.
const _activityBar = 48.0;

ShellRegions _resolve({
  required double width,
  bool primary = true,
  bool secondary = false,
  double preferredPrimary = 280,
  double preferredSecondary = 320,
}) {
  return ShellRegions.resolve(
    breakpoint: DesktopBreakpoint.of(width),
    showPrimarySidebar: primary,
    showSecondarySidebar: secondary,
    preferredPrimaryWidth: preferredPrimary,
    preferredSecondaryWidth: preferredSecondary,
    availableWidth: width,
    activityBarWidth: _activityBar,
  );
}

void main() {
  group('DesktopBreakpoint.of', () {
    test('classifies by width, at the documented boundaries', () {
      expect(DesktopBreakpoint.of(320), DesktopBreakpoint.compact);
      expect(DesktopBreakpoint.of(899.9), DesktopBreakpoint.compact);
      expect(DesktopBreakpoint.of(900), DesktopBreakpoint.standard);
      expect(DesktopBreakpoint.of(1279.9), DesktopBreakpoint.standard);
      expect(DesktopBreakpoint.of(1280), DesktopBreakpoint.wide);
      expect(DesktopBreakpoint.of(3840), DesktopBreakpoint.wide);
    });

    test('a window dragged narrow is compact — this is not a device check', () {
      // Same physical desktop, two window sizes, two answers.
      expect(DesktopBreakpoint.of(1600).primarySidebarIsOverlay, isFalse);
      expect(DesktopBreakpoint.of(700).primarySidebarIsOverlay, isTrue);
    });

    test('the AI panel undocks one step earlier than the Explorer', () {
      expect(DesktopBreakpoint.standard.primarySidebarIsOverlay, isFalse);
      expect(DesktopBreakpoint.standard.secondarySidebarIsOverlay, isTrue);
      expect(DesktopBreakpoint.wide.secondarySidebarIsOverlay, isFalse);
      expect(DesktopBreakpoint.compact.primarySidebarIsOverlay, isTrue);
    });
  });

  group('ShellRegions — which panels are docked', () {
    test('wide docks all three columns', () {
      final r = _resolve(width: 1600, secondary: true);
      expect(r.primaryDocked, isTrue);
      expect(r.secondaryDocked, isTrue);
      expect(r.hasFloatingPanel, isFalse);
    });

    test('standard keeps the Explorer docked and floats the AI panel', () {
      final r = _resolve(width: 1100, secondary: true);
      expect(r.primaryDocked, isTrue);
      expect(r.secondaryDocked, isFalse);
      expect(r.secondaryFloating, isTrue);
      // A strip, not the whole content area — the editor stays visible beside it.
      expect(r.secondaryFillsContentArea, isFalse);
    });

    test('compact floats both, and the AI panel takes the content area', () {
      final r = _resolve(width: 700, secondary: true);
      expect(r.primaryDocked, isFalse);
      expect(r.primaryFloating, isTrue);
      expect(r.secondaryFloating, isTrue);
      // A 200px chat column is not something anyone can work in.
      expect(r.secondaryFillsContentArea, isTrue);
    });

    test('a hidden panel neither docks nor floats at any width', () {
      for (final width in const [700.0, 1100.0, 1600.0]) {
        final r = _resolve(width: width, primary: false, secondary: false);
        expect(r.primaryDocked, isFalse, reason: 'at $width');
        expect(r.primaryFloating, isFalse, reason: 'at $width');
        expect(r.secondaryFloating, isFalse, reason: 'at $width');
        expect(r.hasFloatingPanel, isFalse, reason: 'at $width');
        expect(r.primaryWidth, 0);
        expect(r.secondaryWidth, 0);
      }
    });
  });

  group('ShellRegions — widths', () {
    test('a persisted width is honoured whenever it fits', () {
      final r = _resolve(width: 1600, secondary: true);
      expect(r.primaryWidth, 280);
      expect(r.secondaryWidth, 320);
    });

    test('the editor keeps its minimum rather than the panels keeping theirs', () {
      // 1300px: wide, so both dock. 1300 - 48 = 1252 of content. Honouring
      // 280 + 320 would leave the editor 652 — fine. Push the preferences up
      // until they would not be.
      final r = _resolve(
        width: 1300,
        secondary: true,
        preferredPrimary: 600,
        preferredSecondary: 600,
      );
      final content = 1300 - _activityBar;
      expect(r.primaryWidth + r.secondaryWidth, lessThanOrEqualTo(content - DesktopBreakpoint.minEditorWidth));
      expect(content - r.primaryWidth - r.secondaryWidth,
          greaterThanOrEqualTo(DesktopBreakpoint.minEditorWidth));
    });

    test('two docked panels are clamped against each other, not independently', () {
      // Each would "fit" alone; together they must not squeeze the editor out.
      final both = _resolve(width: 1400, secondary: true, preferredPrimary: 500, preferredSecondary: 500);
      final content = 1400 - _activityBar;
      expect(content - both.primaryWidth - both.secondaryWidth,
          greaterThanOrEqualTo(DesktopBreakpoint.minEditorWidth));
    });

    test('a panel is never clamped below the point of being useful', () {
      // Narrower than minEditorWidth + minPanelWidth: something has to give,
      // and it is not the panel disappearing to a sliver.
      final r = _resolve(width: 500, preferredPrimary: 280);
      expect(r.primaryWidth, greaterThanOrEqualTo(DesktopBreakpoint.minPanelWidth));
    });

    test('dockedPanelWidth never returns more than asked for', () {
      expect(DesktopBreakpoint.dockedPanelWidth(200, 5000), 200);
      expect(DesktopBreakpoint.dockedPanelWidth(200, 0), DesktopBreakpoint.minPanelWidth);
    });
  });
}
