/// Turns an assistant reply into something `POST /api/v3/voice/speak` will accept
/// and that a synthesizer can actually read.
///
/// The gateway rejects `text` longer than [maxSpeakChars] (422). Markdown noise
/// (`**bold**`, fenced code, `[label](url)`) is not speech, so it is stripped
/// before the request rather than spoken aloud.
///
/// Arabic replies keep their script and punctuation (`؟` `؛`); English
/// abbreviation expansions are skipped so the text is not Latinized.
library;

import 'speech_language.dart';

export 'speech_language.dart';

const int maxSpeakChars = 4000;

const _sentenceEndings = '.!?؟؛。';

/// Plain, length-capped speech for a chat reply. Empty when there is nothing left
/// worth sending to the synthesizer.
String prepareSpeechText(String raw, {int maxChars = maxSpeakChars}) {
  var text = raw.replaceAll('\r\n', '\n').trim();
  if (text.isEmpty) return '';

  text = _stripMarkup(text);
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

String _stripMarkup(String text) {
  text = text.replaceAll(RegExp(r'```[\s\S]*?```'), ' ');
  text = text.replaceAllMapped(RegExp(r'`([^`]*)`'), (match) => match[1] ?? '');
  text = text.replaceAllMapped(
    RegExp(r'\[([^\]]+)\]\([^)]*\)'),
    (match) => match[1] ?? '',
  );
  text = text.replaceAll(RegExp(r'^#{1,6}\s+', multiLine: true), '');
  text = text.replaceAll(RegExp(r'^\s*[-*+]\s+', multiLine: true), '');
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
