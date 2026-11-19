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

    // Component label styles below derive from this so they carry the brand
    // font: a bare `TextStyle` in a component theme replaces, rather than
    // merges with, the text theme, and would fall back to the platform font.
    final textTheme = AuraTypography.textTheme(palette);

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      scaffoldBackgroundColor: palette.background,
      colorScheme: colorScheme,
      textTheme: textTheme,
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
      // Desktop and web used to fall back to Material's zoom transition; they
      // now share Android's fade-forwards so every platform but iOS (which
      // keeps its native swipe-back) moves between pages the same way.
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: {
          TargetPlatform.android: FadeForwardsPageTransitionsBuilder(),
          TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
          TargetPlatform.macOS: FadeForwardsPageTransitionsBuilder(),
          TargetPlatform.windows: FadeForwardsPageTransitionsBuilder(),
          TargetPlatform.linux: FadeForwardsPageTransitionsBuilder(),
          TargetPlatform.fuchsia: FadeForwardsPageTransitionsBuilder(),
        },
      ),
      // App bars sit flush on the page background: the page title is the
      // header, not a coloured band (the same reading the web console uses).
      appBarTheme: AppBarTheme(
        backgroundColor: palette.background,
        surfaceTintColor: Colors.transparent,
        foregroundColor: palette.textPrimary,
        elevation: AuraElevation.none,
        scrolledUnderElevation: AuraElevation.none,
        centerTitle: false,
        titleSpacing: AuraSpacing.lg,
        iconTheme: IconThemeData(color: palette.textSecondary),
        actionsIconTheme: IconThemeData(color: palette.textSecondary),
      ),
      listTileTheme: ListTileThemeData(
        iconColor: palette.textSecondary,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AuraRadius.md),
        ),
      ),
      chipTheme: ChipThemeData(
        backgroundColor: palette.surfaceVariant,
        side: BorderSide(color: palette.border),
        shape: const StadiumBorder(),
        labelStyle: textTheme.labelLarge?.copyWith(
          color: palette.textPrimary,
          fontSize: 13,
        ),
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: palette.surface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AuraRadius.dialog),
          side: BorderSide(color: palette.border),
        ),
      ),
      bottomSheetTheme: BottomSheetThemeData(
        backgroundColor: palette.surface,
        surfaceTintColor: Colors.transparent,
        showDragHandle: true,
        dragHandleColor: palette.textDisabled,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(
            top: Radius.circular(AuraRadius.sheet),
          ),
        ),
      ),
      floatingActionButtonTheme: FloatingActionButtonThemeData(
        backgroundColor: palette.brand,
        foregroundColor: palette.onBrand,
        elevation: AuraElevation.raised,
        highlightElevation: AuraElevation.floating,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AuraRadius.lg),
        ),
      ),
      tooltipTheme: TooltipThemeData(
        decoration: BoxDecoration(
          color: palette.textPrimary,
          borderRadius: BorderRadius.circular(AuraRadius.sm),
        ),
        textStyle: textTheme.bodySmall?.copyWith(
          color: palette.background,
          fontSize: 12,
        ),
        waitDuration: const Duration(milliseconds: 400),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: palette.surface,
        surfaceTintColor: Colors.transparent,
        elevation: AuraElevation.none,
        height: 68,
        indicatorColor: palette.brand.withValues(alpha: 0.20),
        indicatorShape: const StadiumBorder(),
        labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
        iconTheme: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return IconThemeData(
            size: 24,
            color: selected
                ? palette.brandOnSurfaceText
                : palette.textSecondary,
          );
        }),
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return textTheme.labelMedium?.copyWith(
            fontSize: 12,
            fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
            color: selected ? palette.textPrimary : palette.textSecondary,
          );
        }),
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: palette.surface,
        elevation: AuraElevation.none,
        indicatorColor: palette.brand.withValues(alpha: 0.20),
        indicatorShape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AuraRadius.md),
        ),
        selectedIconTheme: IconThemeData(
          color: palette.brandOnSurfaceText,
          size: 22,
        ),
        unselectedIconTheme: IconThemeData(
          color: palette.textSecondary,
          size: 22,
        ),
        selectedLabelTextStyle: textTheme.labelLarge?.copyWith(
          color: palette.textPrimary,
          fontSize: 13,
          fontWeight: FontWeight.w600,
        ),
        unselectedLabelTextStyle: textTheme.labelLarge?.copyWith(
          color: palette.textSecondary,
          fontSize: 13,
          fontWeight: FontWeight.w500,
        ),
      ),
    );
  }
}
