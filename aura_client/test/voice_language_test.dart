// Phase V: the language voice input is spoken in. It starts from the device locale and a choice
// the user makes on the live-voice screen is remembered on the device.

import 'package:aura_client/core/ai/voice/voice_language.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

ProviderContainer _container(String deviceLanguage) {
  final container = ProviderContainer(overrides: [
    deviceLanguageCodeProvider.overrideWithValue(deviceLanguage),
  ]);
  addTearDown(container.dispose);
  return container;
}

Future<VoiceLanguage> _settled(ProviderContainer container) async {
  container.read(voiceLanguageProvider);
  await container.read(voiceLanguageProvider.notifier).restored;
  return container.read(voiceLanguageProvider);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('an Arabic-locale device starts in Arabic', () async {
    expect(await _settled(_container('ar')), VoiceLanguage.arabic);
  });

  test('any other device starts in English', () async {
    expect(await _settled(_container('en')), VoiceLanguage.english);
    expect(await _settled(_container('fr')), VoiceLanguage.english);
  });

  test('a saved choice wins over the device default', () async {
    SharedPreferences.setMockInitialValues({VoiceLanguageNotifier.prefsKey: 'ar'});
    expect(await _settled(_container('en')), VoiceLanguage.arabic);
  });

  test('a choice is saved for the next launch', () async {
    final container = _container('en');
    await _settled(container);
    await container.read(voiceLanguageProvider.notifier).set(VoiceLanguage.arabic);

    expect(container.read(voiceLanguageProvider), VoiceLanguage.arabic);
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString(VoiceLanguageNotifier.prefsKey), 'ar');
  });

  test('codes map to the server language codes', () {
    expect(VoiceLanguage.arabic.code, 'ar');
    expect(VoiceLanguage.english.code, 'en');
    expect(VoiceLanguage.fromCode('ar-EG'), VoiceLanguage.arabic);
    expect(VoiceLanguage.fromCode(null), VoiceLanguage.english);
  });
}
