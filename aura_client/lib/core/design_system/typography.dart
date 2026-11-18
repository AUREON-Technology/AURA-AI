import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'aura_palette.dart';

/// Material 3 type scale, built once per palette. Every screen should pull
/// styles from `Theme.of(context).textTheme` rather than constructing
/// one-off `TextStyle(fontSize: 14)` calls — V1 did the latter in ~30 files,
/// which is why its type scale drifted (13/14/15px body text all present
/// with no discernible system).
class AuraTypography {
  AuraTypography._();

  static TextTheme textTheme(AuraPalette palette) {
    return GoogleFonts.outfitTextTheme(
      TextTheme(
        displayLarge: TextStyle(fontSize: 34, fontWeight: FontWeight.w800, height: 1.15, color: palette.textPrimary),
        displayMedium: TextStyle(fontSize: 28, fontWeight: FontWeight.w800, height: 1.2, color: palette.textPrimary),
        headlineLarge: TextStyle(fontSize: 24, fontWeight: FontWeight.w700, height: 1.25, color: palette.textPrimary),
        headlineMedium: TextStyle(fontSize: 20, fontWeight: FontWeight.w700, height: 1.3, color: palette.textPrimary),
        titleLarge: TextStyle(fontSize: 18, fontWeight: FontWeight.w600, height: 1.3, color: palette.textPrimary),
        titleMedium: TextStyle(fontSize: 16, fontWeight: FontWeight.w600, height: 1.35, color: palette.textPrimary),
        titleSmall: TextStyle(fontSize: 14, fontWeight: FontWeight.w600, height: 1.4, color: palette.textPrimary),
        bodyLarge: TextStyle(fontSize: 16, fontWeight: FontWeight.w400, height: 1.5, color: palette.textPrimary),
        bodyMedium: TextStyle(fontSize: 14, fontWeight: FontWeight.w400, height: 1.5, color: palette.textSecondary),
        bodySmall: TextStyle(fontSize: 12, fontWeight: FontWeight.w400, height: 1.5, color: palette.textSecondary),
        labelLarge: TextStyle(fontSize: 14, fontWeight: FontWeight.w600, height: 1.3, color: palette.textPrimary),
        labelMedium: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, height: 1.3, color: palette.textSecondary),
        labelSmall: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, height: 1.3, color: palette.textSecondary),
      ),
    ).apply(fontFamilyFallback: arabicFallback);
  }

  /// Outfit has no Arabic glyphs. Cairo is bundled (pubspec `fonts:`, SIL OFL) so Arabic renders
  /// the same on every platform and offline, instead of whatever system font each OS picks
  /// (docs/plans/arabic-language_plan.md, decision 13).
  static const arabicFallback = <String>['Cairo'];

  /// Primary monospace family. `'monospace'` is the generic alias Android
  /// resolves; the desktop platforms do not have it, which is what
  /// [monoFallback] is for.
  ///
  /// Call sites used to write `fontFamily: 'Courier'` directly — 44 of them
  /// across 20 files. Courier ships with neither Android nor most Linux
  /// desktops, and when a requested family is missing Flutter falls back to
  /// the *default* family, which is proportional: terminal output and unified
  /// diffs silently lose their column alignment on exactly the platforms
  /// where a misread diff costs the most.
  static const String monoFamily = 'monospace';

  /// Tried in order when [monoFamily] does not resolve: macOS/iOS, Windows,
  /// most Linux distributions, then the two Courier spellings as a last
  /// resort before the proportional default.
  static const List<String> monoFallback = <String>[
    'Menlo',
    'Consolas',
    'DejaVu Sans Mono',
    'Courier New',
    'Courier',
  ];

  /// Inline code, file paths, commands, diff bodies — anything where the
  /// character grid carries meaning. Reads at the body-small size so a code
  /// span sits inside a paragraph without changing its line height.
  static TextStyle code(AuraPalette palette, {Color? color, double? fontSize}) {
    return TextStyle(
      fontFamily: monoFamily,
      fontFamilyFallback: monoFallback,
      fontSize: fontSize ?? 12,
      height: 1.45,
      color: color ?? palette.textPrimary,
    );
  }

  /// Terminal and log output. Tighter leading than [code] because a terminal
  /// is read as a block of lines rather than as a span inside prose, and the
  /// extra height wastes a scarce panel.
  static TextStyle terminal(AuraPalette palette, {Color? color, double? fontSize}) {
    return TextStyle(
      fontFamily: monoFamily,
      fontFamilyFallback: monoFallback,
      fontSize: fontSize ?? 12,
      height: 1.3,
      color: color ?? palette.textPrimary,
    );
  }
}
