/// UX_BASELINE_AUDIT.md F-01: the IDE status bar rendered `Ln 1, Col 1` and
/// `LF` as literals regardless of the open buffer. These cover the arithmetic
/// that replaced them.
library;

import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/desktop/shell/editor_position.dart';

void main() {
  group('lineColumnFor', () {
    test('reports 1-based line and column at the start of a buffer', () {
      expect(lineColumnFor('hello', 0), (line: 1, column: 1));
    });

    test('counts columns within the first line', () {
      expect(lineColumnFor('hello', 3), (line: 1, column: 4));
    });

    test('counts lines across LF terminators', () {
      const text = 'one\ntwo\nthree';
      expect(lineColumnFor(text, text.indexOf('two')), (line: 2, column: 1));
      expect(lineColumnFor(text, text.indexOf('three') + 2), (line: 3, column: 3));
    });

    test('a caret just past a newline is column 1 of the next line', () {
      expect(lineColumnFor('a\nb', 2), (line: 2, column: 1));
    });

    test('CRLF counts as one column, not two', () {
      // Caret sits between the \r and the \n of the first terminator. Without
      // the discount this reads Col 3 on a two-character line.
      const text = 'ab\r\ncd';
      expect(lineColumnFor(text, 3), (line: 1, column: 3));
      // And the first character of the next line is still column 1.
      expect(lineColumnFor(text, 4), (line: 2, column: 1));
    });

    test('clamps an offset that outruns the buffer instead of throwing', () {
      // A cursor restored from a previous session can outlive the file it
      // pointed into; a status bar must not throw inside build() for that.
      expect(lineColumnFor('abc', 999), (line: 1, column: 4));
      expect(lineColumnFor('abc', -5), (line: 1, column: 1));
      expect(lineColumnFor('', 0), (line: 1, column: 1));
    });
  });

  group('lineEndingLabelFor', () {
    test('identifies each terminator style', () {
      expect(lineEndingLabelFor('a\nb'), 'LF');
      expect(lineEndingLabelFor('a\r\nb'), 'CRLF');
      expect(lineEndingLabelFor('a\rb'), 'CR');
    });

    test('reports Mixed rather than picking a winner', () {
      expect(lineEndingLabelFor('a\r\nb\nc'), 'Mixed');
      expect(lineEndingLabelFor('a\rb\nc'), 'Mixed');
    });

    test('returns null when the buffer has no terminator to report', () {
      // Which ending a single-line file "would" use is not a fact the buffer
      // contains, so the status bar shows nothing rather than guessing.
      expect(lineEndingLabelFor('no newline here'), isNull);
      expect(lineEndingLabelFor(''), isNull);
    });

    test('a trailing CRLF is CRLF, not Mixed', () {
      expect(lineEndingLabelFor('a\r\n'), 'CRLF');
    });
  });
}
