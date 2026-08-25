import 'package:aura_client/core/ai/voice/speech_text.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('strips markdown so the synthesizer reads the words, not the markup', () {
    const raw = '''
## Status
The **invoice** is ready. See [the report](https://example.com/r) and `total`.
''';
    expect(
      prepareSpeechText(raw),
      'Status The invoice is ready. See the report and total.',
    );
  });

  test('expands abbreviations so they are spoken as words', () {
    expect(
      prepareSpeechText('Ship it, e.g. overnight vs. ground, etc.'),
      contains('for example'),
    );
    expect(prepareSpeechText('This versus that'), isNot(contains('versusus')));
  });

  test('drops fenced code blocks', () {
    const raw = 'Before\n```\nsecret()\n```\nAfter';
    expect(prepareSpeechText(raw), 'Before After.');
  });

  test('caps length at the speak endpoint limit, preferring a sentence break', () {
    final first = '${'Hello world. ' * 10}Done.';
    final rest = 'More. ' * 800;
    final prepared = prepareSpeechText('$first $rest', maxChars: 80);
    expect(prepared.length, lessThanOrEqualTo(80));
    expect(prepared.endsWith('.'), isTrue);
    expect(prepared, isNot(contains('More')));
  });

  test('detects Arabic vs English from script counts', () {
    expect(detectSpeechLanguage('مرحبا، كيف حالك؟'), 'ar');
    expect(detectSpeechLanguage('The quarterly report is ready.'), 'en');
    expect(detectSpeechLanguage('مرحبا hello'), 'ar');
    expect(detectSpeechLanguage('Hello there مرحبا'), 'en');
    expect(detectSpeechLanguage(''), 'en');
    expect(detectSpeechLanguage('...'), 'en');
  });

  test('does not rewrite an Arabic reply into English', () {
    expect(prepareSpeechText('مرحبا، كيف حالك؟'), 'مرحبا، كيف حالك؟');
    expect(prepareSpeechText('النسبة 50% جاهزة؟'), isNot(contains('percent')));
    expect(prepareSpeechText('مَرْحَبًا'), contains('َ'));
    expect(prepareSpeechText('الجواب جاهز؛'), endsWith('؛'));
  });

  test('still expands English abbreviations on English replies', () {
    expect(
      prepareSpeechText('Ship it, e.g. overnight vs. ground, etc.'),
      contains('for example'),
    );
  });

  test('returns empty for markup-only replies', () {
    expect(prepareSpeechText('```\ncode\n```'), isEmpty);
    expect(prepareSpeechText('   '), isEmpty);
  });
}
