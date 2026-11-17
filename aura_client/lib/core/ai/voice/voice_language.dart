import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// The language voice input is spoken in: what the live-voice start frame and the voice-note
/// transcription request send as `language` (Phase V, GAP-020).
///
/// Separate from the UI language, which is English only today: this decides which language the
/// server's Whisper listens for, not what the screens say.
enum VoiceLanguage {
  english('en'),
  arabic('ar');

  const VoiceLanguage(this.code);

  /// The ISO-639-1 code the server's language check accepts.
  final String code;

  static VoiceLanguage fromCode(String? code) {
    final normalised = (code ?? '').toLowerCase();
    return normalised.startsWith('ar') ? VoiceLanguage.arabic : VoiceLanguage.english;
  }
}

/// The device's primary language code (`ar`, `en`, ...). A provider so tests can pin it.
final deviceLanguageCodeProvider = Provider<String>(
  (ref) => PlatformDispatcher.instance.locale.languageCode,
);

/// The user's voice language, remembered on this device.
///
/// Until the user picks one it follows the device: an Arabic-locale phone starts in Arabic,
/// anything else in English. A saved choice replaces that default as soon as it is read back.
class VoiceLanguageNotifier extends Notifier<VoiceLanguage> {
  static const prefsKey = 'voice_language';

  late Future<void> _restored;

  /// Completes once a saved choice (if any) has been read back. A live session awaits it, so
  /// one opened right after launch does not start in the device default by mistake.
  Future<void> get restored => _restored;

  @override
  VoiceLanguage build() {
    _restored = _restore();
    return VoiceLanguage.fromCode(ref.read(deviceLanguageCodeProvider));
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
