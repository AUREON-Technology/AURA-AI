/// Audit F-33: screens printed raw exceptions -- `Text('Error: $e')` in
/// goals, projects, Life Hub and login -- instead of saying what happened and
/// what to do. Failures render through `AuraFailure` / `AuraFailureView` now;
/// this pins it so a raw exception string cannot creep back into a screen.
library;

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// Shapes that put an exception's own text in front of the user.
final _rawErrorPatterns = <String, RegExp>{
  "Text('Error: \$...')": RegExp(r'''Text\(\s*'Error: \$'''),
  'Text("Error: \$...")': RegExp(r'''Text\(\s*"Error: \$'''),
  "'Error: \${...}'": RegExp(r'''['"]Error: \$\{'''),
  "Text('\$e') / Text('\$err')": RegExp(r'''Text\(\s*['"]\$\{?(e|err|error)\}?['"]'''),
  'message: e.toString()': RegExp(r'''message:\s*(e|err|error)\.toString\(\)'''),
  "message: '\$e'": RegExp(r'''message:\s*['"]\$\{?(e|err|error)\}?['"]'''),
};

void main() {
  test('no screen under lib/features renders a raw exception string', () {
    final features = Directory('lib/features');
    expect(features.existsSync(), isTrue, reason: 'run from aura_client/');

    final offenders = <String>[];
    for (final entity in features.listSync(recursive: true)) {
      if (entity is! File || !entity.path.endsWith('.dart')) continue;
      final lines = entity.readAsLinesSync();
      for (var i = 0; i < lines.length; i++) {
        for (final entry in _rawErrorPatterns.entries) {
          if (entry.value.hasMatch(lines[i])) {
            offenders.add('${entity.path}:${i + 1}  ${entry.key}  ${lines[i].trim()}');
          }
        }
      }
    }

    expect(
      offenders,
      isEmpty,
      reason: 'Render failures with AuraFailureView(failure: AuraFailure.from(e)) '
          '(core/network/aura_failure_view.dart), not the exception text:\n'
          '${offenders.join('\n')}',
    );
  });

  test('the guard catches the shapes it is meant to', () {
    const samples = [
      "Text('Error: \$e')",
      'Text("Error: \$e")',
      "Text('Error: \${authState.error}', style: s)",
      "error: (e, _) => Text('\$e'),",
      'message: e.toString(),',
      "message: '\$err',",
    ];
    for (final sample in samples) {
      expect(
        _rawErrorPatterns.values.any((p) => p.hasMatch(sample)),
        isTrue,
        reason: 'not caught: $sample',
      );
    }
  });
}
