// Phase V: the language voice input is spoken in. It follows the app's language (Arabic stage 4)
// until the user picks one on the live-voice screen; that choice is remembered on the device.

import 'package:aura_client/core/ai/voice/voice_language.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/legacy.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

ProviderContainer _container(String appLanguage) {
  final container = ProviderContainer(overrides: [
    appLanguageCodeProvider.overrideWithValue(appLanguage),
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

  test('an app shown in Arabic starts voice in Arabic', () async {
    expect(await _settled(_container('ar')), VoiceLanguage.arabic);
  });

  test('an app in any other language starts voice in English', () async {
    expect(await _settled(_container('en')), VoiceLanguage.english);
    expect(await _settled(_container('fr')), VoiceLanguage.english);
  });

  test('a choice made on the live screen wins over the app language', () async {
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

  test('switching the app language moves voice too, unless the user picked one', () async {
    final app = StateProvider<String>((ref) => 'en');
    final container = ProviderContainer(overrides: [
      appLanguageCodeProvider.overrideWith((ref) => ref.watch(app)),
    ]);
    addTearDown(container.dispose);
    container.listen(voiceLanguageProvider, (_, _) {});
    expect(await _settled(container), VoiceLanguage.english);

    container.read(app.notifier).state = 'ar';
    expect(await _settled(container), VoiceLanguage.arabic);

    await container.read(voiceLanguageProvider.notifier).set(VoiceLanguage.english);
    container.read(app.notifier).state = 'en';
    container.read(app.notifier).state = 'ar';
    expect(await _settled(container), VoiceLanguage.english, reason: 'the explicit choice holds');
  });

  test('codes map to the server language codes', () {
    expect(VoiceLanguage.arabic.code, 'ar');
    expect(VoiceLanguage.english.code, 'en');
    expect(VoiceLanguage.fromCode('ar-EG'), VoiceLanguage.arabic);
    expect(VoiceLanguage.fromCode(null), VoiceLanguage.english);
  });
}
