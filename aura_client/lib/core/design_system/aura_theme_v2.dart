import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'aura_ide_density.dart';
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

  // Computed once, not per-access: these used to be getters recomputing `ColorScheme.fromSeed`
  // (an HCT color-space computation) plus a full `ThemeData` tree on every read -- and
  // `MaterialApp.router` in main.dart reads both `light` and `dark` on every `AuraApp.build()`,
  // not just at startup, so any provider change that rebuilds the app (theme mode, auth state,
  // ...) redid this work for no reason. A `static final` computes it exactly once per app run.
  static final ThemeData light = _build(AuraPalette.light, Brightness.light);
  static final ThemeData dark = _build(AuraPalette.dark, Brightness.dark);

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
      extensions: [palette, AuraIdeDensity.defaultDensity],
      // InkSparkle + ListTile tile/selected colors hides ink (Flutter asserts
      // "ListTile background color or ink splashes may be invisible").
      splashFactory: InkRipple.splashFactory,
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
          padding: const EdgeInsets.symmetric(
            horizontal: AuraSpacing.xl,
            vertical: AuraSpacing.md + 2,
          ),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AuraRadius.md),
          ),
          minimumSize: const Size(44, 44),
          textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: palette.textPrimary,
          side: BorderSide(color: palette.border, width: 1),
          padding: const EdgeInsets.symmetric(
            horizontal: AuraSpacing.xl,
            vertical: AuraSpacing.md + 2,
          ),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AuraRadius.md),
          ),
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
        hintStyle: TextStyle(
          color: palette.textSecondary.withValues(alpha: 0.6),
        ),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: AuraSpacing.lg,
          vertical: AuraSpacing.lg,
        ),
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
      dividerTheme: DividerThemeData(
        color: palette.border,
        thickness: 1,
        space: AuraSpacing.xl,
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: palette.surfaceVariant,
        contentTextStyle: TextStyle(color: palette.textPrimary),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AuraRadius.md),
        ),
      ),
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: FadeForwardsPageTransitionsBuilder(),
          TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
        },
      ),
      navigationBarTheme: NavigationBarThemeData(
        indicatorColor: palette.brand.withValues(alpha: 0.20),
        indicatorShape: const StadiumBorder(),
        iconTheme: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return IconThemeData(
            size: 24,
            color: selected ? palette.brand : palette.textSecondary,
          );
        }),
      ),
    );
  }
}
