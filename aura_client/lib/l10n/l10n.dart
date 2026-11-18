import 'package:flutter/widgets.dart';

import 'generated/app_localizations.dart';
import 'generated/app_localizations_en.dart';

export 'generated/app_localizations.dart';

/// The one way widgets read user-facing strings (audit F-36 / R4.6).
///
/// Strings live in `lib/l10n/app_en.arb`; `flutter gen-l10n` regenerates
/// `lib/l10n/generated/` from it (`flutter: generate: true` also runs it on
/// every `pub get`/build, and the generated files are committed). Adding a
/// locale is then a new `app_<locale>.arb` file, not a code change.
///
/// `context.l10n` falls back to English when no [AppLocalizations] delegate is
/// in the tree, so a widget pumped in a bare `MaterialApp` (most widget tests,
/// and any overlay built above `MaterialApp.router`'s localizations scope)
/// still renders instead of throwing on a null lookup.
extension AuraL10n on BuildContext {
  AppLocalizations get l10n => AppLocalizations.of(this) ?? _englishFallback;
}

final AppLocalizations _englishFallback = AppLocalizationsEn();

AppLocalizations? _current;

/// The strings of the language on screen, for code that has no [BuildContext]: failure copy
/// built from an error (`core/network/aura_failure.dart`), notifiers and services that produce
/// user-facing text. `AuraApp`'s builder keeps it current; before the first frame, and in a test
/// that never builds `AuraApp`, it is English -- the same fallback as [AuraL10n.l10n].
///
/// Prefer `context.l10n` wherever a context exists: a string read from here when it is *built*
/// keeps that language until rebuilt, so only use it for text produced at the moment it is shown.
AppLocalizations get currentL10n => _current ?? _englishFallback;

/// Called by `AuraApp`'s builder with the localizations in scope (null resets to English).
void setCurrentL10n(AppLocalizations? localizations) => _current = localizations;
