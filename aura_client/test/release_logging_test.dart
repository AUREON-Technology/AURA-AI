import 'package:aura_client/main.dart' show silenceDebugLogging;
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';

/// A release build silences `debugPrint`, which Flutter does not strip from release builds and
/// which the app uses to log server response bodies.
void main() {
  test('silenceDebugLogging makes debugPrint a no-op', () {
    final original = debugPrint;
    final printed = <String?>[];
    debugPrint = (String? message, {int? wrapWidth}) => printed.add(message);
    addTearDown(() => debugPrint = original);

    debugPrint('before');
    silenceDebugLogging();
    debugPrint('{"detail":"server body"}');

    expect(printed, ['before']);
  });
}
