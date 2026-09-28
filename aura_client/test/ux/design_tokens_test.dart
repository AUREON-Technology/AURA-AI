import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/design_system/aura_palette.dart';
import 'package:aura_client/core/design_system/contrast.dart';
import 'package:aura_client/core/design_system/tokens.dart';
import 'package:aura_client/core/design_system/typography.dart';

/// Phase 1 gate for the semantic token set (docs/ux/DESIGN_SYSTEM.md).
///
/// These assert the *shape* of the system rather than specific pretty
/// values: that the spacing scale has no gaps a call site would have to
/// invent a value to fill, that every surface with a named radius/elevation
/// has one, and that the monospace roles resolve to an actual fallback chain
/// rather than to a single family that half the target platforms lack.
void main() {
  group('AuraSpacing', () {
    test('is the full documented scale, in order, with no gaps', () {
      expect(
        const [
          AuraSpacing.xs,
          AuraSpacing.sm,
          AuraSpacing.md,
          AuraSpacing.lg,
          AuraSpacing.xl,
          AuraSpacing.xxl,
          AuraSpacing.xxxl,
          AuraSpacing.xxxxl,
        ],
        [4.0, 8.0, 12.0, 16.0, 24.0, 32.0, 48.0, 64.0],
      );
    });
  });

  group('AuraRadius', () {
    test('dialog and sheet resolve to steps already on the scale', () {
      expect(AuraRadius.dialog, AuraRadius.lg);
      expect(AuraRadius.sheet, AuraRadius.xl);
    });
  });

  group('AuraElevation', () {
    test('semantic names cover the four steps in ascending order', () {
      expect(
        const [
          AuraElevation.flat,
          AuraElevation.resting,
          AuraElevation.raised,
          AuraElevation.floating,
        ],
        [0.0, 2.0, 6.0, 12.0],
      );
    });
  });

  group('AuraTypography monospace', () {
    for (final entry in {'light': AuraPalette.light, 'dark': AuraPalette.dark}.entries) {
      test('${entry.key}: code() and terminal() carry the fallback chain', () {
        for (final style in [
          AuraTypography.code(entry.value),
          AuraTypography.terminal(entry.value),
        ]) {
          expect(style.fontFamily, AuraTypography.monoFamily);
          // The whole point of the token: a single family is not enough,
          // because 'monospace' does not resolve on macOS or Windows and
          // Flutter falls back to a *proportional* default when it cannot
          // resolve one — which is how a diff loses column alignment.
          expect(style.fontFamilyFallback, isNotEmpty);
          expect(style.fontFamilyFallback, containsAll(<String>['Menlo', 'Consolas']));
        }
      });
    }

    test('terminal leads tighter than code', () {
      expect(
        AuraTypography.terminal(AuraPalette.dark).height!,
        lessThan(AuraTypography.code(AuraPalette.dark).height!),
      );
    });

    test('a caller can override colour without losing the mono family', () {
      final style = AuraTypography.code(AuraPalette.dark, color: const Color(0xFFFF0000));
      expect(style.color, const Color(0xFFFF0000));
      expect(style.fontFamily, AuraTypography.monoFamily);
    });
  });

  group('pending is a distinct semantic role', () {
    for (final entry in {'light': AuraPalette.light, 'dark': AuraPalette.dark}.entries) {
      final palette = entry.value;

      test('${entry.key}: pending is not an alias of warning, info or brand', () {
        expect(palette.pending, isNot(palette.warning));
        expect(palette.pending, isNot(palette.info));
        expect(palette.pending, isNot(palette.brand));
      });

      test('${entry.key}: pending is legible as text on surface', () {
        expect(contrastRatio(palette.pending, palette.surface), greaterThanOrEqualTo(4.5));
      });
    }

    test('copyWith and lerp carry pending', () {
      expect(AuraPalette.dark.copyWith(pending: const Color(0xFF123456)).pending,
          const Color(0xFF123456));
      final mid = AuraPalette.dark.lerp(AuraPalette.light, 0.5);
      expect(mid.pending, Color.lerp(AuraPalette.dark.pending, AuraPalette.light.pending, 0.5));
    });
  });
}
