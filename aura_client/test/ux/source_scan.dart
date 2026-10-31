import 'dart:io';

/// Shared helpers for the source-scanning guard tests (audit F-36): the IconButton tooltip lint,
/// the hard-coded `Text('...')` ratchet and the RTL physical-direction guard.
///
/// They read `lib/` as text rather than through the analyzer, so they stay cheap; [maskDart]
/// is what keeps that honest -- comments and string contents are blanked out first, so a
/// `(` inside a string or a commented-out `IconButton(` cannot skew what is counted.

/// The `.dart` sources under [root], excluding generated code.
List<File> dartSources(String root) {
  final files = Directory(root)
      .listSync(recursive: true)
      .whereType<File>()
      .where((f) => f.path.endsWith('.dart'))
      .where((f) => !f.path.contains('${Platform.pathSeparator}generated${Platform.pathSeparator}'))
      .where((f) => !f.path.endsWith('.g.dart'))
      .toList()
    ..sort((a, b) => a.path.compareTo(b.path));
  return files;
}

/// [source] with every comment and the contents of every string literal replaced by spaces.
///
/// Offsets and newlines are preserved, and the quote characters themselves are kept, so a
/// match in the masked text maps back to the same line of the original and `Text('` still
/// looks like a string-literal argument. Interpolations are blanked along with the rest of the
/// string; that is fine here, because nothing these guards look for lives inside one.
String maskDart(String source) {
  final out = StringBuffer();
  var i = 0;
  final n = source.length;

  void blank(int from, int to) {
    for (var k = from; k < to; k++) {
      out.write(source[k] == '\n' ? '\n' : ' ');
    }
  }

  while (i < n) {
    final c = source[i];
    final next = i + 1 < n ? source[i + 1] : '';
    if (c == '/' && next == '/') {
      final end = source.indexOf('\n', i);
      final stop = end == -1 ? n : end;
      blank(i, stop);
      i = stop;
      continue;
    }
    if (c == '/' && next == '*') {
      final end = source.indexOf('*/', i + 2);
      final stop = end == -1 ? n : end + 2;
      blank(i, stop);
      i = stop;
      continue;
    }
    if (c == "'" || c == '"') {
      final raw = i > 0 && source[i - 1] == 'r';
      final triple = source.startsWith(c * 3, i);
      final quote = triple ? c * 3 : c;
      out.write(quote);
      var j = i + quote.length;
      while (j < n && !source.startsWith(quote, j)) {
        if (!raw && source[j] == r'\') j++;
        j++;
      }
      blank(i + quote.length, j.clamp(0, n));
      if (j < n) out.write(quote);
      i = (j + quote.length).clamp(0, n);
      continue;
    }
    out.write(c);
    i++;
  }
  return out.toString();
}

/// The index just past the `)` that closes the `(` ending at [openParenEnd] in masked source.
int closingParen(String masked, int openParenEnd) {
  var depth = 1;
  var i = openParenEnd;
  while (i < masked.length && depth > 0) {
    final c = masked[i];
    if (c == '(' || c == '[' || c == '{') depth++;
    if (c == ')' || c == ']' || c == '}') depth--;
    i++;
  }
  return i;
}

/// Whether [args] (the masked text between a call's parentheses) passes a named argument
/// matching [name] at the call's own nesting level -- not inside a nested widget's arguments.
bool hasTopLevelNamedArg(String args, RegExp name) {
  var depth = 0;
  for (var i = 0; i < args.length; i++) {
    final c = args[i];
    if (c == '(' || c == '[' || c == '{') depth++;
    if (c == ')' || c == ']' || c == '}') depth--;
    if (depth != 0) continue;
    final prevIsIdent = i > 0 && RegExp(r'[\w$]').hasMatch(args[i - 1]);
    if (prevIsIdent) continue;
    final m = name.matchAsPrefix(args, i);
    if (m != null) return true;
  }
  return false;
}

/// 1-based line number of [offset] in [text].
int lineOf(String text, int offset) => '\n'.allMatches(text.substring(0, offset)).length + 1;
