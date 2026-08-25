/// Turns an assistant reply into something `POST /api/v3/voice/speak` will accept
/// and that a synthesizer can actually read.
///
/// The gateway rejects `text` longer than [maxSpeakChars] (422). Markdown noise
/// (`**bold**`, fenced code, `[label](url)`) is not speech, so it is stripped
/// before the request rather than spoken aloud.
///
/// Structure is the one thing this must *not* throw away. A synthesizer pauses
/// only at punctuation, and markdown carries its structure in newlines and
/// bullet markers -- so deleting those and collapsing the whitespace hands the
/// server a single run-on clause it has no way to re-segment, however good its
/// own rewrite stage is. [_punctuateStructure] turns each boundary into a
/// sentence terminator instead; it mirrors `_punctuate_structure` in the
/// backend's `aura_core/adapters/spoken_text.py`, and the two should stay in
/// step.
///
/// Number and date wording is deliberately *not* duplicated here. The server
/// runs its own rewrite before synthesis, so currency and ISO dates are fixed
/// there once, for every client.
///
/// Arabic replies keep their script and punctuation (`؟` `؛`); English
/// abbreviation expansions are skipped so the text is not Latinized.
library;

import 'speech_language.dart';

export 'speech_language.dart';

const int maxSpeakChars = 4000;

const _sentenceEndings = '.!?؟؛。';

/// Trailing separators a structural terminator replaces rather than follows --
/// "Send it,." is worse than the comma was.
const _clauseTrailers = ',;:،';

final _headingLine = RegExp(r'^#{1,6}\s+');
final _bulletLine = RegExp(r'^\s*(?:[-*+]|\d+\.)\s+');

/// Plain, length-capped speech for a chat reply. Empty when there is nothing left
/// worth sending to the synthesizer.
String prepareSpeechText(String raw, {int maxChars = maxSpeakChars}) {
  var text = raw.replaceAll('\r\n', '\n').trim();
  if (text.isEmpty) return '';

  text = _stripMarkup(text);
  text = _punctuateStructure(text);
  if (detectSpeechLanguage(text) == 'en') {
    text = _expandEnglishAbbreviations(text);
  }
  text = text.replaceAllMapped(
    RegExp(r'([.!?؟。]){2,}'),
    (match) => match[1] ?? match[0] ?? '',
  );
  text = text.replaceAll(RegExp(r'\s+'), ' ').trim();
  if (text.isEmpty) return '';
  if (!_sentenceEndings.contains(text[text.length - 1])) {
    text = '$text.';
  }
  if (text.length <= maxChars) return text;
  return _clipToSentence(text, maxChars);
}

/// Strips markup that is not structural. Headings and bullets survive this pass
/// -- they carry the pause structure [_punctuateStructure] needs, and are
/// removed there once their boundary has been recorded as punctuation.
String _stripMarkup(String text) {
  text = text.replaceAll(RegExp(r'```[\s\S]*?```'), ' ');
  text = text.replaceAllMapped(RegExp(r'`([^`]*)`'), (match) => match[1] ?? '');
  text = text.replaceAllMapped(
    RegExp(r'\[([^\]]+)\]\([^)]*\)'),
    (match) => match[1] ?? '',
  );
  text = text.replaceAllMapped(
    RegExp(r'\*\*([^*]+)\*\*'),
    (match) => match[1] ?? '',
  );
  text = text.replaceAllMapped(
    RegExp(r'\*(\S+?)\*'),
    (match) => match[1] ?? '',
  );
  text = text.replaceAll(RegExp(r'https?://\S+', caseSensitive: false), ' ');
  return text.replaceAll('_', ' ');
}

/// Ends a structural unit so the synthesizer pauses after it.
String _terminate(String line) {
  var text = line.trimRight();
  while (text.isNotEmpty &&
      _clauseTrailers.contains(text[text.length - 1])) {
    text = text.substring(0, text.length - 1).trimRight();
  }
  if (text.isEmpty) return '';
  if (_sentenceEndings.contains(text[text.length - 1])) return text;
  return '$text.';
}

/// Converts markdown structure into sentence terminators.
///
/// Lines are gathered into blocks and terminated once per block, not once per
/// line. A block ends at a blank line, a heading, or the start of a new list
/// item -- so a soft-wrapped paragraph stays one sentence ("The invoice is
/// ready and. the total..." is exactly the artefact this removes) while a
/// lead-in line before a list still gets its pause.
String _punctuateStructure(String text) {
  final blocks = <String>[];
  final pending = <String>[];

  void flush() {
    if (pending.isEmpty) return;
    final line = _terminate(pending.join(' '));
    pending.clear();
    if (line.isNotEmpty) blocks.add(line);
  }

  for (final rawLine in text.split('\n')) {
    final line = rawLine.trim();
    if (line.isEmpty) {
      flush();
      continue;
    }
    if (_headingLine.hasMatch(line)) {
      flush();
      final heading = line.replaceFirst(_headingLine, '').trim();
      if (heading.isNotEmpty) {
        pending.add(heading);
        flush();
      }
      continue;
    }
    if (_bulletLine.hasMatch(line)) {
      flush();
      final item = line.replaceFirst(_bulletLine, '').trim();
      if (item.isNotEmpty) pending.add(item);
      continue;
    }
    // A plain line continues whatever block is open -- a paragraph, or a
    // wrapped list item.
    pending.add(line);
  }

  flush();
  return blocks.join(' ');
}

String _expandEnglishAbbreviations(String text) {
  text = text.replaceAll(RegExp(r'\be\.g\.', caseSensitive: false), 'for example');
  text = text.replaceAll(RegExp(r'\bi\.e\.', caseSensitive: false), 'that is');
  text = text.replaceAll(RegExp(r'\betc\.', caseSensitive: false), 'et cetera');
  text = text.replaceAll(RegExp(r'\bvs\.', caseSensitive: false), 'versus');
  text = text.replaceAll(RegExp(r'\bvs\b', caseSensitive: false), 'versus');
  text = text.replaceAll('%', ' percent');
  text = text.replaceAll('&', ' and ');
  return text;
}

String _clipToSentence(String text, int maxChars) {
  final clipped = text.substring(0, maxChars);
  var sentenceEnd = -1;
  for (final mark in ['. ', '? ', '! ', '؟ ', '؛ ', '。']) {
    final index = clipped.lastIndexOf(mark);
    if (index > sentenceEnd) sentenceEnd = index;
  }
  if (sentenceEnd >= maxChars ~/ 2) {
    return clipped.substring(0, sentenceEnd + 1).trim();
  }
  final space = clipped.lastIndexOf(' ');
  if (space > 0) return clipped.substring(0, space).trim();
  return clipped.trim();
}
