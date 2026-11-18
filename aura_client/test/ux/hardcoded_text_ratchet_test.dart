import 'package:flutter_test/flutter_test.dart';

import 'source_scan.dart';

// Ratchet on hard-coded `Text('...')` literals under lib/features/ (audit F-36 / R4.6).
//
// User-facing strings belong in lib/l10n/app_en.arb and are read through `context.l10n`
// (lib/l10n/l10n.dart), so a second locale is a translation file rather than a code change.
// Chat, sign-in, the You/settings screens and the shared design-system components were
// extracted first; the features left (goals, projects, automations, health, finance, calendar,
// analytics, the engineering pages, ...) still hold literals.
//
// The ceiling is today's count. It may only go DOWN: when you extract a feature's strings,
// lower it here in the same change. Raising it needs a reason that survives review. A
// `Text('$value')` that only interpolates data counts too -- it is cheap to route through
// `Text(value)` instead, and the scan stays a plain lexical check.
const int _ceiling = 28;

final _textLiteral = RegExp(r'''(?<![\w$.])Text\(\s*['"]''');

int _countLiterals() {
  var count = 0;
  for (final file in dartSources('lib/features')) {
    count += _textLiteral.allMatches(maskDart(file.readAsStringSync())).length;
  }
  return count;
}

void main() {
  test('the masker keeps the quote so a literal argument is still visible', () {
    final masked = maskDart("Text('Hello (world)'); // Text('in a comment')\nText(name);");
    expect(_textLiteral.allMatches(masked), hasLength(1));
  });

  test('hard-coded Text literals under lib/features/ do not grow', () {
    final count = _countLiterals();
    expect(count, greaterThan(0), reason: 'scanned nothing: is the test running from aura_client/?');
    expect(
      count,
      lessThanOrEqualTo(_ceiling),
      reason: 'New user-facing text goes in lib/l10n/app_en.arb and is read via context.l10n.',
    );
  });

  test('the ceiling is tight, so an extraction cannot be silently undone', () {
    expect(
      _countLiterals(),
      _ceiling,
      reason: 'The count went down -- lower _ceiling in this test to lock the gain in.',
    );
  });
}
