import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'app_localizations_en.dart';

// ignore_for_file: type=lint

/// Callers can lookup localized strings with an instance of AppLocalizations
/// returned by `AppLocalizations.of(context)`.
///
/// Applications need to include `AppLocalizations.delegate()` in their app's
/// `localizationDelegates` list, and the locales they support in the app's
/// `supportedLocales` list. For example:
///
/// ```dart
/// import 'generated/app_localizations.dart';
///
/// return MaterialApp(
///   localizationsDelegates: AppLocalizations.localizationsDelegates,
///   supportedLocales: AppLocalizations.supportedLocales,
///   home: MyApplicationHome(),
/// );
/// ```
///
/// ## Update pubspec.yaml
///
/// Please make sure to update your pubspec.yaml to include the following
/// packages:
///
/// ```yaml
/// dependencies:
///   # Internationalization support.
///   flutter_localizations:
///     sdk: flutter
///   intl: any # Use the pinned version from flutter_localizations
///
///   # Rest of dependencies
/// ```
///
/// ## iOS Applications
///
/// iOS applications define key application metadata, including supported
/// locales, in an Info.plist file that is built into the application bundle.
/// To configure the locales supported by your app, you’ll need to edit this
/// file.
///
/// First, open your project’s ios/Runner.xcworkspace Xcode workspace file.
/// Then, in the Project Navigator, open the Info.plist file under the Runner
/// project’s Runner folder.
///
/// Next, select the Information Property List item, select Add Item from the
/// Editor menu, then select Localizations from the pop-up menu.
///
/// Select and expand the newly-created Localizations item then, for each
/// locale your application supports, add a new item and select the locale
/// you wish to add from the pop-up menu in the Value field. This list should
/// be consistent with the languages listed in the AppLocalizations.supportedLocales
/// property.
abstract class AppLocalizations {
  AppLocalizations(String locale)
    : localeName = intl.Intl.canonicalizedLocale(locale.toString());

  final String localeName;

  static AppLocalizations? of(BuildContext context) {
    return Localizations.of<AppLocalizations>(context, AppLocalizations);
  }

  static const LocalizationsDelegate<AppLocalizations> delegate =
      _AppLocalizationsDelegate();

  /// A list of this localizations delegate along with the default localizations
  /// delegates.
  ///
  /// Returns a list of localizations delegates containing this delegate along with
  /// GlobalMaterialLocalizations.delegate, GlobalCupertinoLocalizations.delegate,
  /// and GlobalWidgetsLocalizations.delegate.
  ///
  /// Additional delegates can be added by appending to this list in
  /// MaterialApp. This list does not have to be used at all if a custom list
  /// of delegates is preferred or required.
  static const List<LocalizationsDelegate<dynamic>> localizationsDelegates =
      <LocalizationsDelegate<dynamic>>[
        delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
      ];

  /// A list of this localizations delegate's supported locales.
  static const List<Locale> supportedLocales = <Locale>[Locale('en')];

  /// Today screen header, shown before noon
  ///
  /// In en, this message translates to:
  /// **'Good morning'**
  String get greetingMorning;

  /// Today screen header, shown 12pm-6pm
  ///
  /// In en, this message translates to:
  /// **'Good afternoon'**
  String get greetingAfternoon;

  /// Today screen header, shown after 6pm
  ///
  /// In en, this message translates to:
  /// **'Good evening'**
  String get greetingEvening;

  /// Placeholder text in the Today screen's AI capture bar
  ///
  /// In en, this message translates to:
  /// **'Ask AURA anything, or capture a task…'**
  String get captureBarHint;

  /// Section header for the unified tasks+events list
  ///
  /// In en, this message translates to:
  /// **'Due today'**
  String get dueToday;

  /// Link to see the full task list
  ///
  /// In en, this message translates to:
  /// **'View all'**
  String get viewAll;

  /// Empty state title for the due-today list
  ///
  /// In en, this message translates to:
  /// **'Nothing due today'**
  String get nothingDueToday;

  /// Empty state subtitle for the due-today list
  ///
  /// In en, this message translates to:
  /// **'You\'re all caught up.'**
  String get allCaughtUp;

  /// Error state for the due-today list
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load today\'s items'**
  String get couldNotLoadDueToday;

  /// Stat tile label
  ///
  /// In en, this message translates to:
  /// **'Active tasks'**
  String get activeTasks;

  /// Stat tile label
  ///
  /// In en, this message translates to:
  /// **'Water today'**
  String get waterToday;

  /// Stat tile label
  ///
  /// In en, this message translates to:
  /// **'Spent this period'**
  String get spentThisPeriod;

  /// Section header for the system status card
  ///
  /// In en, this message translates to:
  /// **'AI system status'**
  String get aiSystemStatus;

  /// Status row label for backend reachability
  ///
  /// In en, this message translates to:
  /// **'AURA AI service'**
  String get serverInference;

  /// Status value when the AI service is reachable
  ///
  /// In en, this message translates to:
  /// **'Ready'**
  String get statusReady;

  /// Status value when the on-device model failed to load
  ///
  /// In en, this message translates to:
  /// **'Unavailable'**
  String get statusUnavailable;

  /// Status row label
  ///
  /// In en, this message translates to:
  /// **'Local data'**
  String get localData;

  /// Status row value describing local storage encryption
  ///
  /// In en, this message translates to:
  /// **'Encrypted (AES-256-GCM)'**
  String get encryptedAesGcm;

  /// Icon button tooltip for the You destination
  ///
  /// In en, this message translates to:
  /// **'You'**
  String get settingsTooltip;

  /// Bottom navigation label
  ///
  /// In en, this message translates to:
  /// **'Today'**
  String get navToday;

  /// Bottom navigation label
  ///
  /// In en, this message translates to:
  /// **'Copilot'**
  String get navCopilot;

  /// Bottom navigation label
  ///
  /// In en, this message translates to:
  /// **'Life Hub'**
  String get navLifeHub;

  /// Bottom navigation label
  ///
  /// In en, this message translates to:
  /// **'You'**
  String get navYou;

  /// Life Hub screen title
  ///
  /// In en, this message translates to:
  /// **'Life Hub'**
  String get lifeHubTitle;

  /// You screen title
  ///
  /// In en, this message translates to:
  /// **'You'**
  String get youTitle;

  /// Knowledge library title
  ///
  /// In en, this message translates to:
  /// **'Knowledge'**
  String get knowledgeTitle;

  /// Memory screen title
  ///
  /// In en, this message translates to:
  /// **'Memory'**
  String get memoryTitle;

  /// Feedback screen title
  ///
  /// In en, this message translates to:
  /// **'Feedback'**
  String get feedbackTitle;

  /// Appearance screen title
  ///
  /// In en, this message translates to:
  /// **'Appearance'**
  String get appearanceTitle;

  /// Engine status screen title
  ///
  /// In en, this message translates to:
  /// **'Engine status'**
  String get engineStatusTitle;

  /// Onboarding next button
  ///
  /// In en, this message translates to:
  /// **'Next'**
  String get onboardingNext;

  /// Onboarding finish button
  ///
  /// In en, this message translates to:
  /// **'Get started'**
  String get onboardingGetStarted;

  /// Bell icon tooltip when there are no unread items
  ///
  /// In en, this message translates to:
  /// **'Notifications'**
  String get notificationsTooltip;

  /// Bell icon tooltip/semantic label when there are unread items
  ///
  /// In en, this message translates to:
  /// **'{count} unread notifications'**
  String unreadNotifications(int count);
}

class _AppLocalizationsDelegate
    extends LocalizationsDelegate<AppLocalizations> {
  const _AppLocalizationsDelegate();

  @override
  Future<AppLocalizations> load(Locale locale) {
    return SynchronousFuture<AppLocalizations>(lookupAppLocalizations(locale));
  }

  @override
  bool isSupported(Locale locale) =>
      <String>['en'].contains(locale.languageCode);

  @override
  bool shouldReload(_AppLocalizationsDelegate old) => false;
}

AppLocalizations lookupAppLocalizations(Locale locale) {
  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'en':
      return AppLocalizationsEn();
  }

  throw FlutterError(
    'AppLocalizations.delegate failed to load unsupported locale "$locale". This is likely '
    'an issue with the localizations generation tool. Please file an issue '
    'on GitHub with a reproducible sample app and the gen-l10n configuration '
    'that was used.',
  );
}
