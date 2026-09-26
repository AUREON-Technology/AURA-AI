import 'package:flutter/material.dart';

/// Semantic color roles for one theme (light or dark). Widgets should always
/// read colors through [AuraPalette.of(context)], never as hardcoded hex —
/// this is what makes light/dark (and future brand re-skins) a config
/// change instead of a per-screen rewrite, unlike V1's single hardcoded
/// `AuraTheme` class.
///
/// `xOnSurface` colors are contrast-checked against [surface]/[background]
/// for text use (WCAG AA, verified by `test/design_system_contrast_test.dart`).
/// `xBrand` colors are decorative fills (buttons, icons, gradients) and are
/// only held to the looser 3:1 UI-component contrast requirement.
@immutable
class AuraPalette extends ThemeExtension<AuraPalette> {
  final Color background;
  final Color surface;
  final Color surfaceVariant;
  final Color border;

  final Color textPrimary;
  final Color textSecondary;
  final Color textDisabled;

  /// Decorative brand fill — buttons, active nav icons, gradients.
  final Color brand;
  final Color brandOnSurfaceText;
  final Color onBrand;

  final Color success;
  final Color warning;
  final Color danger;
  final Color info;

  final LinearGradient brandGradient;
  final LinearGradient glassGradient;

  const AuraPalette({
    required this.background,
    required this.surface,
    required this.surfaceVariant,
    required this.border,
    required this.textPrimary,
    required this.textSecondary,
    required this.textDisabled,
    required this.brand,
    required this.brandOnSurfaceText,
    required this.onBrand,
    required this.success,
    required this.warning,
    required this.danger,
    required this.info,
    required this.brandGradient,
    required this.glassGradient,
  });

  static AuraPalette of(BuildContext context) {
    return Theme.of(context).extension<AuraPalette>() ?? dark;
  }

  static const AuraPalette dark = AuraPalette(
    background: Color(0xFF0F0C1B),
    surface: Color(0xFF17162B),
    surfaceVariant: Color(0xFF221F3B),
    border: Color(0x1F94A3B8),
    textPrimary: Color(0xFFF8FAFC),
    textSecondary: Color(0xFF94A3B8),
    textDisabled: Color(0xFF64748B),
    // Deliberately off the Tailwind scale. indigo-500 (#6366F1) puts white at
    // 4.47:1 — just under AA for the 15px button label — and indigo-600
    // (#4F46E5) drops `brand` to 2.82:1 as a fill on dark `surface` and
    // collides with light's `brandOnSurfaceText`. No step does both.
    brand: Color(0xFF5F61EE),
    brandOnSurfaceText: Color(0xFF818CF8), // indigo-400: brighter for AA on dark surfaces
    onBrand: Colors.white,
    success: Color(0xFF10B981),
    warning: Color(0xFFF59E0B),
    danger: Color(0xFFF87171),
    info: Color(0xFF38BDF8),
    brandGradient: LinearGradient(
      colors: [Color(0xFF5F61EE), Color(0xFF8B5CF6)],
      begin: Alignment.topLeft,
      end: Alignment.bottomRight,
    ),
    glassGradient: LinearGradient(
      colors: [Color(0x1F6366F1), Color(0x0A8B5CF6)],
      begin: Alignment.topLeft,
      end: Alignment.bottomRight,
    ),
  );

  static const AuraPalette light = AuraPalette(
    background: Color(0xFFF8FAFC),
    surface: Color(0xFFFFFFFF),
    surfaceVariant: Color(0xFFF1F5F9),
    border: Color(0x14000000),
    textPrimary: Color(0xFF0F172A),
    textSecondary: Color(0xFF334155), // slate-700: safe AA margin on white
    textDisabled: Color(0xFF94A3B8),
    brand: Color(0xFF5F61EE), // see AuraPalette.dark for why this is off-scale
    brandOnSurfaceText: Color(0xFF4F46E5), // indigo-600: AA-safe as text/links on light
    onBrand: Colors.white,
    success: Color(0xFF047857),
    warning: Color(0xFFB45309),
    danger: Color(0xFFDC2626),
    info: Color(0xFF0369A1), // sky-700: sky-600 was 4.10:1 on white, below AA
    brandGradient: LinearGradient(
      colors: [Color(0xFF5F61EE), Color(0xFF8B5CF6)],
      begin: Alignment.topLeft,
      end: Alignment.bottomRight,
    ),
    glassGradient: LinearGradient(
      colors: [Color(0x0F6366F1), Color(0x058B5CF6)],
      begin: Alignment.topLeft,
      end: Alignment.bottomRight,
    ),
  );

  @override
  AuraPalette copyWith({
    Color? background,
    Color? surface,
    Color? surfaceVariant,
    Color? border,
    Color? textPrimary,
    Color? textSecondary,
    Color? textDisabled,
    Color? brand,
    Color? brandOnSurfaceText,
    Color? onBrand,
    Color? success,
    Color? warning,
    Color? danger,
    Color? info,
    LinearGradient? brandGradient,
    LinearGradient? glassGradient,
  }) {
    return AuraPalette(
      background: background ?? this.background,
      surface: surface ?? this.surface,
      surfaceVariant: surfaceVariant ?? this.surfaceVariant,
      border: border ?? this.border,
      textPrimary: textPrimary ?? this.textPrimary,
      textSecondary: textSecondary ?? this.textSecondary,
      textDisabled: textDisabled ?? this.textDisabled,
      brand: brand ?? this.brand,
      brandOnSurfaceText: brandOnSurfaceText ?? this.brandOnSurfaceText,
      onBrand: onBrand ?? this.onBrand,
      success: success ?? this.success,
      warning: warning ?? this.warning,
      danger: danger ?? this.danger,
      info: info ?? this.info,
      brandGradient: brandGradient ?? this.brandGradient,
      glassGradient: glassGradient ?? this.glassGradient,
    );
  }

  @override
  AuraPalette lerp(ThemeExtension<AuraPalette>? other, double t) {
    if (other is! AuraPalette) return this;
    return AuraPalette(
      background: Color.lerp(background, other.background, t)!,
      surface: Color.lerp(surface, other.surface, t)!,
      surfaceVariant: Color.lerp(surfaceVariant, other.surfaceVariant, t)!,
      border: Color.lerp(border, other.border, t)!,
      textPrimary: Color.lerp(textPrimary, other.textPrimary, t)!,
      textSecondary: Color.lerp(textSecondary, other.textSecondary, t)!,
      textDisabled: Color.lerp(textDisabled, other.textDisabled, t)!,
      brand: Color.lerp(brand, other.brand, t)!,
      brandOnSurfaceText: Color.lerp(brandOnSurfaceText, other.brandOnSurfaceText, t)!,
      onBrand: Color.lerp(onBrand, other.onBrand, t)!,
      success: Color.lerp(success, other.success, t)!,
      warning: Color.lerp(warning, other.warning, t)!,
      danger: Color.lerp(danger, other.danger, t)!,
      info: Color.lerp(info, other.info, t)!,
      brandGradient: LinearGradient.lerp(brandGradient, other.brandGradient, t)!,
      glassGradient: LinearGradient.lerp(glassGradient, other.glassGradient, t)!,
    );
  }

  /// All (foreground, background, label) triples that must hold WCAG AA.
  /// Read by `test/design_system_contrast_test.dart` — add a new text color
  /// here whenever one is added above, so a regression fails CI instead of
  /// shipping. A color left off this list is not checked at all, which is how
  /// `onBrand/brand` and `info/surface` both shipped below AA.
  List<(Color, Color, String)> get aaTextPairs => [
        (textPrimary, background, 'textPrimary/background'),
        (textPrimary, surface, 'textPrimary/surface'),
        (textSecondary, background, 'textSecondary/background'),
        (textSecondary, surface, 'textSecondary/surface'),
        (brandOnSurfaceText, surface, 'brandOnSurfaceText/surface'),
        // A fill, not a surface: the button label is the text and `brand` is
        // its ground, so this pair is what gates the primary button.
        (onBrand, brand, 'onBrand/brand'),
        (success, surface, 'success/surface'),
        (warning, surface, 'warning/surface'),
        (danger, surface, 'danger/surface'),
        (info, surface, 'info/surface'),
      ];
}
