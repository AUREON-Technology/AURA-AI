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
    );
  }
}
