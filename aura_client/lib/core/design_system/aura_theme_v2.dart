import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'aura_palette.dart';
import 'tokens.dart';
import 'typography.dart';

/// V2 theme assembly. Screens migrated to the V2 design system should use
/// `AuraThemeV2.light`/`.dark` (wired in `main.dart` via `ThemeMode.system`)
/// instead of the V1 `AuraTheme.darkTheme` constant, which stays in place
/// unmodified until every screen is migrated (see Doc/V2_BLUEPRINT.md §13
/// Phase 2 — this is a deliberate incremental migration, not a big-bang
/// swap that would break the ~20 screens still on V1 styling).
class AuraThemeV2 {
  AuraThemeV2._();

  static ThemeData get light => _build(AuraPalette.light, Brightness.light);
  static ThemeData get dark => _build(AuraPalette.dark, Brightness.dark);

  static ThemeData _build(AuraPalette palette, Brightness brightness) {
    final colorScheme = ColorScheme.fromSeed(
      seedColor: palette.brand,
      brightness: brightness,
      primary: palette.brand,
      onPrimary: palette.onBrand,
      surface: palette.surface,
      onSurface: palette.textPrimary,
      error: palette.danger,
    );

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      scaffoldBackgroundColor: palette.background,
      colorScheme: colorScheme,
      textTheme: AuraTypography.textTheme(palette),
      extensions: [palette],
      splashFactory: InkSparkle.splashFactory,
      cardTheme: CardThemeData(
        color: palette.surface,
        elevation: AuraElevation.none,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AuraRadius.lg),
          side: BorderSide(color: palette.border, width: 1),
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: palette.brand,
          foregroundColor: palette.onBrand,
          elevation: AuraElevation.none,
          padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.xl, vertical: AuraSpacing.md + 2),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AuraRadius.md)),
          minimumSize: const Size(44, 44),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: palette.textPrimary,
          side: BorderSide(color: palette.border, width: 1),
          padding: const EdgeInsets.symmetric(horizontal: AuraSpacing.xl, vertical: AuraSpacing.md + 2),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AuraRadius.md)),
          minimumSize: const Size(44, 44),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: palette.brandOnSurfaceText,
          minimumSize: const Size(44, 44),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: palette.surfaceVariant,
        labelStyle: TextStyle(color: palette.textSecondary),
        hintStyle: TextStyle(color: palette.textSecondary.withValues(alpha: 0.6)),
        contentPadding: const EdgeInsets.symmetric(horizontal: AuraSpacing.lg, vertical: AuraSpacing.lg),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AuraRadius.md),
          borderSide: BorderSide.none,
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AuraRadius.md),
          borderSide: BorderSide(color: palette.border, width: 1),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AuraRadius.md),
          borderSide: BorderSide(color: palette.brand, width: 2),
        ),
      ),
      dividerTheme: DividerThemeData(color: palette.border, thickness: 1, space: AuraSpacing.xl),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: palette.surfaceVariant,
        contentTextStyle: TextStyle(color: palette.textPrimary),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(AuraRadius.md)),
      ),
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: FadeForwardsPageTransitionsBuilder(),
          TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
        },
      ),
    );
  }
}
