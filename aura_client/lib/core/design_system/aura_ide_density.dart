/// IDE-specific density tokens for AURA Desktop.
///
/// Applied only under [DesktopShell] via ThemeExtension to keep
/// mobile/tablet layouts unchanged. These tokens control the compact,
/// information-dense layout expected in a professional IDE.
library;

import 'dart:ui' show lerpDouble;

import 'package:flutter/material.dart';
import 'tokens.dart';

@immutable
class AuraIdeDensity extends ThemeExtension<AuraIdeDensity> {
  const AuraIdeDensity({
    required this.activityBarWidth,
    required this.primarySidebarWidth,
    required this.secondarySidebarWidth,
    required this.titleBarHeight,
    required this.tabHeight,
    required this.statusBarHeight,
    required this.bottomPanelHeight,
    required this.editorPadding,
    required this.treeRowHeight,
    required this.compactControlHeight,
    required this.spacingScale,
  });

  /// Width of the far-left activity bar (Explorer, Search, Source Control, etc.)
  final double activityBarWidth;

  /// Width of the primary sidebar (Explorer panel)
  final double primarySidebarWidth;

  /// Width of the secondary sidebar (Agent panel, AI, etc.)
  final double secondarySidebarWidth;

  /// Height of the window title bar / tab strip area
  final double titleBarHeight;

  /// Height of editor tabs
  final double tabHeight;

  /// Height of the bottom status bar
  final double statusBarHeight;

  /// Default height of the bottom panel (Terminal, Output, Problems)
  final double bottomPanelHeight;

  /// Padding inside the editor content area
  final EdgeInsets editorPadding;

  /// Row height in file tree / explorer
  final double treeRowHeight;

  /// Height for compact buttons, inputs, etc.
  final double compactControlHeight;

  /// Multiplier applied to [AuraSpacing] values for IDE density
  /// (e.g., 0.75 for tighter spacing than mobile)
  final double spacingScale;

  static const AuraIdeDensity defaultDensity = AuraIdeDensity(
    activityBarWidth: 48.0,
    primarySidebarWidth: 280.0,
    secondarySidebarWidth: 320.0,
    titleBarHeight: 36.0,
    tabHeight: 32.0,
    statusBarHeight: 24.0,
    bottomPanelHeight: 200.0,
    editorPadding: EdgeInsets.all(12.0),
    treeRowHeight: 22.0,
    compactControlHeight: 28.0,
    spacingScale: 0.75,
  );

  static const AuraIdeDensity compact = AuraIdeDensity(
    activityBarWidth: 44.0,
    primarySidebarWidth: 240.0,
    secondarySidebarWidth: 280.0,
    titleBarHeight: 32.0,
    tabHeight: 28.0,
    statusBarHeight: 22.0,
    bottomPanelHeight: 180.0,
    editorPadding: EdgeInsets.all(8.0),
    treeRowHeight: 20.0,
    compactControlHeight: 26.0,
    spacingScale: 0.65,
  );

  @override
  AuraIdeDensity copyWith({
    double? activityBarWidth,
    double? primarySidebarWidth,
    double? secondarySidebarWidth,
    double? titleBarHeight,
    double? tabHeight,
    double? statusBarHeight,
    double? bottomPanelHeight,
    EdgeInsets? editorPadding,
    double? treeRowHeight,
    double? compactControlHeight,
    double? spacingScale,
  }) {
    return AuraIdeDensity(
      activityBarWidth: activityBarWidth ?? this.activityBarWidth,
      primarySidebarWidth: primarySidebarWidth ?? this.primarySidebarWidth,
      secondarySidebarWidth: secondarySidebarWidth ?? this.secondarySidebarWidth,
      titleBarHeight: titleBarHeight ?? this.titleBarHeight,
      tabHeight: tabHeight ?? this.tabHeight,
      statusBarHeight: statusBarHeight ?? this.statusBarHeight,
      bottomPanelHeight: bottomPanelHeight ?? this.bottomPanelHeight,
      editorPadding: editorPadding ?? this.editorPadding,
      treeRowHeight: treeRowHeight ?? this.treeRowHeight,
      compactControlHeight: compactControlHeight ?? this.compactControlHeight,
      spacingScale: spacingScale ?? this.spacingScale,
    );
  }

  @override
  AuraIdeDensity lerp(ThemeExtension<AuraIdeDensity>? other, double t) {
    if (other is! AuraIdeDensity) return this;
    return AuraIdeDensity(
      activityBarWidth: lerpDouble(activityBarWidth, other.activityBarWidth, t)!,
      primarySidebarWidth: lerpDouble(primarySidebarWidth, other.primarySidebarWidth, t)!,
      secondarySidebarWidth: lerpDouble(secondarySidebarWidth, other.secondarySidebarWidth, t)!,
      titleBarHeight: lerpDouble(titleBarHeight, other.titleBarHeight, t)!,
      tabHeight: lerpDouble(tabHeight, other.tabHeight, t)!,
      statusBarHeight: lerpDouble(statusBarHeight, other.statusBarHeight, t)!,
      bottomPanelHeight: lerpDouble(bottomPanelHeight, other.bottomPanelHeight, t)!,
      editorPadding: EdgeInsets.lerp(editorPadding, other.editorPadding, t)!,
      treeRowHeight: lerpDouble(treeRowHeight, other.treeRowHeight, t)!,
      compactControlHeight: lerpDouble(compactControlHeight, other.compactControlHeight, t)!,
      spacingScale: lerpDouble(spacingScale, other.spacingScale, t)!,
    );
  }

}

extension AuraIdeDensityContext on BuildContext {
  /// Convenience accessor for IDE density tokens.
  AuraIdeDensity get ideDensity => Theme.of(this).extension<AuraIdeDensity>() ?? AuraIdeDensity.defaultDensity;
}