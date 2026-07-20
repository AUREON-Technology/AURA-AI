import 'package:flutter_test/flutter_test.dart';
import 'package:aura_client/core/design_system/aura_palette.dart';
import 'package:aura_client/core/design_system/contrast.dart';

// Design-token contrast gate: fails CI if any text/surface color pair in
// the V2 design system regresses below WCAG AA (4.5:1). Run as part of the
// normal `flutter test` suite, so no separate tooling/CI step is needed.
void main() {
  group('AuraPalette WCAG AA contrast', () {
    for (final entry in {'light': AuraPalette.light, 'dark': AuraPalette.dark}.entries) {
      final themeName = entry.key;
      final palette = entry.value;

      for (final pair in palette.aaTextPairs) {
        final (fg, bg, label) = pair;
        test('$themeName: $label meets 4.5:1', () {
          final ratio = contrastRatio(fg, bg);
          expect(
            ratio,
            greaterThanOrEqualTo(4.5),
            reason: '$label contrast is ${ratio.toStringAsFixed(2)}:1, below WCAG AA (4.5:1)',
          );
        });
      }
    }
  });
}
