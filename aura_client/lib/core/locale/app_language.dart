import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../platform/client_language.dart';
import '../security/aura_secure_storage.dart';

const _kAppLanguageKey = 'aura_app_language';

/// The language the app is shown in (docs/plans/arabic-language_plan.md, decision 2).
///
/// [system] follows the device; the other two override it. Arabic is `ar` with no region on
/// purpose: `intl` formats `ar` with Western digits and `ar_EG` with Arabic-Indic ones, and the
/// owner chose Western digits everywhere (decision 7). A device set to `ar-EG` still resolves to
/// this locale, because it is the supported one with that language.
enum AppLanguage {
  system(null),
  english('en'),
  arabic('ar');

  const AppLanguage(this.code);

  /// The language code sent to the backend and passed to `MaterialApp.locale`; null follows the
  /// device.
  final String? code;

  Locale? get locale => code == null ? null : Locale(code!);

  static AppLanguage fromStored(String? raw) => switch (raw) {
        'en' => AppLanguage.english,
        'ar' => AppLanguage.arabic,
        _ => AppLanguage.system,
      };
}

/// Persists the user's choice next to the theme mode (same storage, same pattern) and makes
/// every request report it: [ClientLanguage.override] reads the current choice, so the
/// `X-Client-Language` header always names what is on screen.
class AppLanguageController extends Notifier<AppLanguage> {
  @override
  AppLanguage build() {
    ClientLanguage.override = () => state.code;
    ref.onDispose(() => ClientLanguage.override = null);
    _load();
    return AppLanguage.system;
  }

  Future<void> _load() async {
    final language = AppLanguage.fromStored(await auraSecureStorage.read(key: _kAppLanguageKey));
    if (state != language) state = language;
  }

  Future<void> setLanguage(AppLanguage language) async {
    state = language;
    await auraSecureStorage.write(key: _kAppLanguageKey, value: language.code ?? 'system');
  }
}

final appLanguageProvider =
    NotifierProvider<AppLanguageController, AppLanguage>(AppLanguageController.new);
