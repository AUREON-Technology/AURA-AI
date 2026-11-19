import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../locale/app_language.dart';
import '../../platform/client_language.dart';

/// The language voice input is spoken in: what the live-voice start frame and the voice-note
/// transcription request send as `language` (Phase V, GAP-020).
///
/// It follows the app's display language (Appearance → Language) until the user picks one on
/// the live voice screen, so someone reading the app in English can still speak Arabic. It
/// decides which language the server's Whisper listens for, not what the screens say.
enum VoiceLanguage {
  english('en'),
  arabic('ar');

  const VoiceLanguage(this.code);

  /// The ISO-639-1 code the server's language check accepts.
  final String code;

  /// The language's own name, in its own script, whatever the UI language: someone looking for
  /// Arabic finds «العربية» in an English app (as the Appearance picker does).
  String get nativeName => switch (this) {
        VoiceLanguage.english => 'English',
        VoiceLanguage.arabic => 'العربية',
      };

  static VoiceLanguage fromCode(String? code) {
    final normalised = (code ?? '').toLowerCase();
    return normalised.startsWith('ar') ? VoiceLanguage.arabic : VoiceLanguage.english;
  }
}

/// The app's display language code (`ar`, `en`, ...): the in-app choice, else the device's.
/// Rebuilt when the in-app choice changes. A provider so tests can pin it.
final appLanguageCodeProvider = Provider<String>((ref) {
  ref.watch(appLanguageProvider); // installs ClientLanguage.override and tracks its changes
  return ClientLanguage.current();
});

/// The user's voice language, remembered on this device.
///
/// Until the user picks one on the live voice screen it follows the app's language: an app shown
/// in Arabic listens for Arabic, anything else for English. A saved choice replaces that default
/// as soon as it is read back, and keeps applying when the app language changes later.
class VoiceLanguageNotifier extends Notifier<VoiceLanguage> {
  static const prefsKey = 'voice_language';

  late Future<void> _restored;

  /// Completes once a saved choice (if any) has been read back. A live session awaits it, so
  /// one opened right after launch does not start in the app-language default by mistake.
  Future<void> get restored => _restored;

  @override
  VoiceLanguage build() {
    _restored = _restore();
    return VoiceLanguage.fromCode(ref.watch(appLanguageCodeProvider));
  }

  Future<void> _restore() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = prefs.getString(prefsKey);
      if (saved != null && ref.mounted) state = VoiceLanguage.fromCode(saved);
    } catch (e) {
      // No preferences store (some test hosts): the device default stands.
      debugPrint('[VoiceLanguage] restore failed: $e');
    }
  }

  Future<void> set(VoiceLanguage language) async {
    await _restored; // a late read-back must not overwrite this choice
    state = language;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(prefsKey, language.code);
    } catch (e) {
      debugPrint('[VoiceLanguage] save failed: $e');
    }
  }
}

final voiceLanguageProvider =
    NotifierProvider<VoiceLanguageNotifier, VoiceLanguage>(VoiceLanguageNotifier.new);
