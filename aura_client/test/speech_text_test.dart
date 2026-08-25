import 'package:aura_client/core/ai/voice/speech_text.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('strips markdown so the synthesizer reads the words, not the markup', () {
    const raw = '''
## Status
The **invoice** is ready. See [the report](https://example.com/r) and `total`.
''';
    // The heading ends in a period: it is a structural boundary, and without one
    // the synthesizer runs "Status" straight into "The invoice" on one breath.
    expect(
      prepareSpeechText(raw),
      'Status. The invoice is ready. See the report and total.',
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
    // The removed block leaves a structural gap, so the two halves are spoken as
    // separate sentences rather than run together.
    expect(prepareSpeechText(raw), 'Before. After.');
  });

  group('pause structure', () {
    test('each bullet becomes its own sentence', () {
      const raw = 'Next steps\n- Send the invoice\n- Archive the thread';
      expect(
        prepareSpeechText(raw),
        'Next steps. Send the invoice. Archive the thread.',
      );
    });

    test('numbered and asterisk items are separated too', () {
      expect(prepareSpeechText('1. Call the bank\n2. Send the form'),
          'Call the bank. Send the form.');
      expect(prepareSpeechText('* First item\n* Second item'),
          'First item. Second item.');
    });

    test('a soft-wrapped paragraph is not chopped into sentences', () {
      // The regression this guards: a terminator per line would produce
      // "The invoice is ready and. the total is settled."
      expect(
        prepareSpeechText('The invoice is ready and\nthe total is settled.'),
        'The invoice is ready and the total is settled.',
      );
    });

    test('a wrapped list item stays one sentence', () {
      const raw = '- Send the invoice and\n  archive the thread\n- Log the hours';
      expect(
        prepareSpeechText(raw),
        'Send the invoice and archive the thread. Log the hours.',
      );
    });

    test('a trailing comma is replaced, not doubled', () {
      final spoken = prepareSpeechText('- Send it,\n- Archive it');
      expect(spoken, isNot(contains(',.')));
      expect(spoken, 'Send it. Archive it.');
    });

    test('an item already ending in punctuation is left alone', () {
      expect(prepareSpeechText('- Is it ready?\n- Yes!'), 'Is it ready? Yes!');
    });

    test('a paragraph break ends a sentence', () {
      expect(
        prepareSpeechText('The invoice is ready\n\nNothing else is due'),
        'The invoice is ready. Nothing else is due.',
      );
    });

    test('Arabic bullets get pause points as well', () {
      final spoken = prepareSpeechText('- الفاتورة جاهزة\n- لا شيء آخر');
      expect('.'.allMatches(spoken).length, 2);
    });
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
