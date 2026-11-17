import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

// Ratchet on the legacy, dark-only `AuraTheme` constants (`core/theme/theme.dart`) under
// `lib/features/` (audit F-32 / R4.2). They are fixed colours tuned for the dark theme, so on the
// light theme -- the default -- they can render text near-invisible (`AuraTheme.textPrimary`
// 0xFFF8FAFC on a white card was ~1.05:1). New code reads colours through
// `AuraPalette.of(context)` instead.
//
// The ceilings below are today's counts. A count may only go DOWN: when you migrate a widget to
// the palette, lower its number here in the same change. Raising one needs a reason that
// survives review.
const Map<String, int> _ceilings = {
  'accentAmber': 6,
  'accentIndigo': 6,
  'accentTeal': 8,
  'background': 2,
  'cardBg': 8,
  'primaryGradient': 0,
  'textSecondary': 7,
  // Driven to zero by F-32: every remaining use was primary text on a theme surface.
  'textPrimary': 0,
};

final _legacyUse = RegExp(r'\bAuraTheme\.(\w+)');

Map<String, int> _countLegacyUses() {
  final counts = <String, int>{};
  final files = Directory('lib/features')
      .listSync(recursive: true)
      .whereType<File>()
      .where((f) => f.path.endsWith('.dart'));
  for (final file in files) {
    for (final match in _legacyUse.allMatches(file.readAsStringSync())) {
      final member = match.group(1)!;
      counts[member] = (counts[member] ?? 0) + 1;
    }
  }
  return counts;
}

void main() {
  test('legacy AuraTheme colour uses under lib/features/ do not grow', () {
    final counts = _countLegacyUses();
    expect(counts, isNotEmpty, reason: 'scanned no usages: is the test running from aura_client/?');

    final over = <String>[];
    for (final entry in counts.entries) {
      final ceiling = _ceilings[entry.key] ?? 0;
      if (entry.value > ceiling) {
        over.add('AuraTheme.${entry.key}: ${entry.value} uses (ceiling $ceiling)');
      }
    }
    expect(
      over,
      isEmpty,
      reason: 'Use AuraPalette.of(context) instead of the dark-only AuraTheme colours.',
    );
  });

  test('ceilings are tight, so a migration cannot be silently undone', () {
    final counts = _countLegacyUses();
    final slack = <String>[];
    for (final entry in _ceilings.entries) {
      final actual = counts[entry.key] ?? 0;
      if (actual < entry.value) {
        slack.add('AuraTheme.${entry.key}: ${entry.value} -> $actual');
      }
    }
    expect(
      slack,
      isEmpty,
      reason: 'Usages went down -- lower these ceilings in this test to lock the gain in.',
    );
  });
}
