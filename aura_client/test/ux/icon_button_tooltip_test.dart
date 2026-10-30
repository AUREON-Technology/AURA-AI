import 'package:flutter_test/flutter_test.dart';

import 'source_scan.dart';

// Audit F-36 / R4.6: an icon-only button has no accessible name unless it is given one. Before
// this test, 21 of the app's IconButtons had none, so TalkBack/VoiceOver announced them as just
// "button". Every `IconButton(...)` (and its `.filled` / `.filledTonal` / `.outlined` variants)
// under lib/ must pass `tooltip:` -- which Flutter also exposes as the semantics label -- at the
// call's own level, not merely somewhere inside a nested widget.
//
// Allowlist: `path:line` of a call that genuinely cannot carry a tooltip, each with the reason.
// Keep it empty unless there is no alternative; a Semantics wrapper is not a substitute here,
// because a tooltip also helps sighted mouse and keyboard users.
const Set<String> _allowlist = {};

final _iconButtonCall = RegExp(r'(?<![\w$.])IconButton(\.(filled|filledTonal|outlined))?\(');
final _tooltipArg = RegExp(r'tooltip\s*:');

List<String> _iconButtonsWithoutTooltip({bool applyAllowlist = true}) {
  final missing = <String>[];
  for (final file in dartSources('lib')) {
    final masked = maskDart(file.readAsStringSync());
    for (final m in _iconButtonCall.allMatches(masked)) {
      final end = closingParen(masked, m.end);
      final args = masked.substring(m.end, end - 1);
      if (hasTopLevelNamedArg(args, _tooltipArg)) continue;
      final where = '${file.path}:${lineOf(masked, m.start)}';
      if (!applyAllowlist || !_allowlist.contains(where)) missing.add(where);
    }
  }
  return missing;
}

void main() {
  test('the scanner finds IconButtons at all (is the test running from aura_client/?)', () {
    var total = 0;
    for (final file in dartSources('lib')) {
      total += _iconButtonCall.allMatches(maskDart(file.readAsStringSync())).length;
    }
    expect(total, greaterThan(50));
  });

  test('the scanner ignores comments, strings and nested tooltips', () {
    const source = '''
// IconButton(icon: x)
final a = 'IconButton(';
final b = IconButton(icon: Tooltip(message: 'm', tooltip: 'nested'), onPressed: f);
final c = IconButton(onPressed: f, tooltip: 'ok', icon: Icon(Icons.add));
final d = IconButton.styleFrom(padding: EdgeInsets.zero);
''';
    final masked = maskDart(source);
    final calls = _iconButtonCall.allMatches(masked).toList();
    expect(calls, hasLength(2), reason: 'comment, string literal and styleFrom are not calls');
    final withTooltip = [
      for (final m in calls)
        hasTopLevelNamedArg(masked.substring(m.end, closingParen(masked, m.end) - 1), _tooltipArg),
    ];
    expect(withTooltip, [false, true], reason: 'a tooltip on a nested widget does not count');
  });

  test('every IconButton under lib/ has a tooltip', () {
    expect(
      _iconButtonsWithoutTooltip(),
      isEmpty,
      reason: 'Give each IconButton a `tooltip:` (read it from context.l10n) so screen readers '
          'and hover users get a name for it.',
    );
  });

  test('allowlist entries still point at a tooltip-less IconButton', () {
    final missing = _iconButtonsWithoutTooltip(applyAllowlist: false).toSet();
    expect(_allowlist.difference(missing), isEmpty, reason: 'Remove stale allowlist entries.');
  });
}
