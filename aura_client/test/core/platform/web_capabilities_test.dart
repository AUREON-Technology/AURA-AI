import 'package:aura_client/core/platform/web_capabilities.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('a browser has no voice note, no live voice and no local daemon', () {
    expect(voiceNoteAvailable(isWeb: true), isFalse);
    expect(liveVoiceRouteAvailable(isWeb: true), isFalse);
    expect(localDaemonAvailable(isWeb: true), isFalse);
  });

  test('every other build keeps all three', () {
    expect(voiceNoteAvailable(isWeb: false), isTrue);
    expect(liveVoiceRouteAvailable(isWeb: false), isTrue);
    expect(localDaemonAvailable(isWeb: false), isTrue);
  });

  test('the test host is not a browser', () {
    expect(voiceNoteAvailable(), isTrue);
    expect(liveVoiceRouteAvailable(), isTrue);
  });
}
