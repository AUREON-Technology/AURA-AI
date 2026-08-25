/// Detect which language a reply should be spoken in from the text itself.
///
/// Matches the gateway's `detect_speech_language`: Arabic-script count vs Latin
/// `[A-Za-z]`. Arabic wins when its count is greater than zero and at least the
/// Latin count.
library;

final _arabic = RegExp(
  r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]',
);
final _latin = RegExp(r'[A-Za-z]');

/// `ar` or `en`. Empty, punctuation-only, and Latin-majority strings are English.
String detectSpeechLanguage(String text) {
  if (text.isEmpty) return 'en';
  final arabic = _arabic.allMatches(text).length;
  final latin = _latin.allMatches(text).length;
  if (arabic > 0 && arabic >= latin) return 'ar';
  return 'en';
}
