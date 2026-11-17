import 'dart:ui' show Locale, PlatformDispatcher;

import 'package:flutter/widgets.dart' show WidgetsBinding;

/// The header the backend reads the app's display language from
/// (`aura_core/text/language.py`, `services/user_language.py`). A chat reply mirrors the
/// language the user writes in; this decides only for a message with no clear language of its own
/// (a link, a number, code), and it is what the server writes the daily briefing in
/// (docs/plans/arabic-language_plan.md).
const clientLanguageHeader = 'X-Client-Language';

/// Where the language the app is showing comes from.
///
/// Today that is the device language. Stage 2's in-app picker (System / English / العربية) sets
/// [override] to the user's choice, so every request reports what is actually on screen without
/// each API client knowing about the picker. Tests set it too.
abstract final class ClientLanguage {
  /// The user's in-app choice as a language code (`ar`, `en`), or null to follow the device.
  static String? Function()? override;

  /// The language code to send: the in-app choice, else the device's primary language
  /// (`ar` for `ar-EG`). Never empty; the backend ignores a language it does not support.
  static String current() {
    final chosen = override?.call()?.trim();
    if (chosen != null && chosen.isNotEmpty) return chosen;
    final device = _deviceLocale().languageCode.trim();
    return device.isEmpty || device == 'und' ? 'en' : device;
  }

  /// Through the binding when there is one (the app always has one by the time it sends a
  /// request, and widget tests override the locale there); the raw dispatcher otherwise.
  static Locale _deviceLocale() {
    try {
      return WidgetsBinding.instance.platformDispatcher.locale;
    } catch (_) {
      return PlatformDispatcher.instance.locale;
    }
  }
}
