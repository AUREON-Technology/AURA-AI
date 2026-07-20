import 'dart:math' as math;
import 'dart:ui' show Color;

/// WCAG 2.1 relative luminance + contrast ratio helpers.
/// Used both by design-token definitions and by `tool/check_contrast.dart`,
/// which fails CI if a token pair regresses below WCAG AA
/// (4.5:1 for normal text, 3:1 for large text / UI components).
double relativeLuminance(Color c) {
  double toLinear(double v) => v <= 0.03928 ? v / 12.92 : math.pow((v + 0.055) / 1.055, 2.4).toDouble();

  final r = toLinear(c.r);
  final g = toLinear(c.g);
  final b = toLinear(c.b);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

double contrastRatio(Color a, Color b) {
  final l1 = relativeLuminance(a);
  final l2 = relativeLuminance(b);
  final lighter = l1 > l2 ? l1 : l2;
  final darker = l1 > l2 ? l2 : l1;
  return (lighter + 0.05) / (darker + 0.05);
}

bool meetsAA(Color foreground, Color background, {bool largeText = false}) {
  final ratio = contrastRatio(foreground, background);
  return ratio >= (largeText ? 3.0 : 4.5);
}
