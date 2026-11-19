import 'package:aura_client/core/locale/text_direction.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

// A message reads in its own direction, whatever the app language (docs/plans/
// arabic-language_plan.md, stage 2): first strong character, outside code and URLs.

void main() {
  test('the first strong character decides', () {
    expect(directionOfText('ازيك عامل ايه؟'), TextDirection.rtl);
    expect(directionOfText('hello there'), TextDirection.ltr);
    expect(directionOfText('شغّل git status لو سمحت'), TextDirection.rtl);
    expect(directionOfText('Run الكود ده'), TextDirection.ltr);
    expect(directionOfText('  «مرحبا»'), TextDirection.rtl);
  });

  test('code, links and URLs do not decide', () {
    expect(directionOfText('`git status` بيقول ايه؟'), TextDirection.rtl);
    expect(directionOfText('```dart\nvoid main() {}\n```\nده الكود'), TextDirection.rtl);
    expect(directionOfText('https://example.com/en/docs شوف ده'), TextDirection.rtl);
    expect(directionOfText('[المستند](https://example.com/doc)'), TextDirection.rtl);
  });

  test('nothing strong leaves the ambient direction', () {
    expect(directionOfText(''), isNull);
    expect(directionOfText('12500 🙂 !!'), isNull);
    expect(directionOfText('`ls -la`'), isNull);
  });

  testWidgets('LtrContent stays left-to-right inside a right-to-left app', (tester) async {
    late TextDirection seen;
    await tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.rtl,
        child: LtrContent(
          child: Builder(builder: (context) {
            seen = Directionality.of(context);
            return const SizedBox();
          }),
        ),
      ),
    );
    expect(seen, TextDirection.ltr);
  });
}
