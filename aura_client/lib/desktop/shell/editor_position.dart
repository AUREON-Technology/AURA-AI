/// Pure helpers behind the editor readouts in the IDE status bar.
///
/// Kept out of the widget so the arithmetic is unit-testable without
/// pumping a frame — and because the values these produce are the ones the
/// status bar previously faked (`Ln 1, Col 1`, `LF`, regardless of the
/// buffer), which is the kind of thing that should have a test.
library;

/// One-based line and column for [offset] within [text], the way every
/// editor and every compiler diagnostic reports a position.
///
/// [offset] is clamped into the buffer: a stale cursor restored from a
/// previous session can outrun a file that shrank on disk since, and
/// reporting the last valid position is better than throwing inside a
/// `build()`.
///
/// A CRLF terminator counts as one column, not two — the `\r` is part of
/// the line break rather than a character the caret can sit between, so
/// counting it would make every column on a CRLF file read one too high.
({int line, int column}) lineColumnFor(String text, int offset) {
  final safeOffset = offset.clamp(0, text.length);
  var line = 1;
  var lineStart = 0;
  for (var i = 0; i < safeOffset; i++) {
    if (text.codeUnitAt(i) == 0x0A) {
      line++;
      lineStart = i + 1;
    }
  }
  var column = safeOffset - lineStart + 1;
  // Discount a `\r` immediately before the caret when it belongs to a CRLF
  // pair the caret has not yet crossed.
  if (safeOffset > lineStart &&
      text.codeUnitAt(safeOffset - 1) == 0x0D &&
      safeOffset < text.length &&
      text.codeUnitAt(safeOffset) == 0x0A) {
    column -= 1;
  }
  return (line: line, column: column);
}

/// The line terminator [text] actually uses, as the label an editor shows:
/// `LF`, `CRLF`, `CR`, or `Mixed`.
///
/// Returns `null` when the buffer has no terminator at all (a single line,
/// or an empty file). The caller shows nothing in that case rather than
/// guessing — which line ending a file "would" use once it grows a second
/// line is not a fact the buffer contains.
String? lineEndingLabelFor(String text) {
  var hasCrlf = false;
  var hasLoneLf = false;
  var hasLoneCr = false;

  // A single manual scan rather than three regexes: the lone-LF and lone-CR
  // cases both need to know about their neighbour, and expressing that with
  // look-around reads worse than just looking at the next character.
  for (var i = 0; i < text.length; i++) {
    final unit = text.codeUnitAt(i);
    if (unit == 0x0D) {
      if (i + 1 < text.length && text.codeUnitAt(i + 1) == 0x0A) {
        hasCrlf = true;
        i++; // consume the LF so it is not also counted as a lone one
      } else {
        hasLoneCr = true;
      }
    } else if (unit == 0x0A) {
      hasLoneLf = true;
    }
  }

  final kinds = [
    if (hasCrlf) 'CRLF',
    if (hasLoneLf) 'LF',
    if (hasLoneCr) 'CR',
  ];
  if (kinds.isEmpty) return null;
  if (kinds.length > 1) return 'Mixed';
  return kinds.first;
}
