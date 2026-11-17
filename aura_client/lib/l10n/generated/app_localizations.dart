import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'app_localizations_ar.dart';
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
  static const List<Locale> supportedLocales = <Locale>[
    Locale('ar'),
    Locale('en'),
  ];

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

  /// Onboarding page 1 title
  ///
  /// In en, this message translates to:
  /// **'AURA is your copilot'**
  String get onboardingCopilotTitle;

  /// Onboarding page 1 body
  ///
  /// In en, this message translates to:
  /// **'Talk to it. It remembers, searches your documents, and proposes actions — you confirm the writes.'**
  String get onboardingCopilotBody;

  /// Onboarding page 2 title: where requests are processed
  ///
  /// In en, this message translates to:
  /// **'Your private data stays with AURA'**
  String get onboardingPrivacyTitle;

  /// Onboarding page 2 body. Must match the backend's data-classification routing (ADR-030): sensitive content stays on self-hosted models; hosted providers are opt-in and see only internal-classified requests
  ///
  /// In en, this message translates to:
  /// **'Nothing is processed on this phone. Your documents, memories and files are only read by AI models AURA runs itself. Other messages may be answered by an approved hosted AI provider, if the server’s operator allows it.'**
  String get onboardingPrivacyBody;

  /// Onboarding page 3 title: the four main tabs
  ///
  /// In en, this message translates to:
  /// **'Today, Copilot, Life Hub, You'**
  String get onboardingTourTitle;

  /// Onboarding page 3 body
  ///
  /// In en, this message translates to:
  /// **'Capture from anywhere. Browse tasks, calendar, finance, and health in Life Hub. Account tools live under You.'**
  String get onboardingTourBody;

  /// Onboarding button that skips the remaining intro pages
  ///
  /// In en, this message translates to:
  /// **'Skip'**
  String get onboardingSkip;

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

  /// You screen section label
  ///
  /// In en, this message translates to:
  /// **'Account'**
  String get settingsSectionAccount;

  /// You screen section label
  ///
  /// In en, this message translates to:
  /// **'Desktop'**
  String get settingsSectionDesktop;

  /// You screen section label
  ///
  /// In en, this message translates to:
  /// **'Your AURA'**
  String get settingsSectionYourAura;

  /// You screen section label: the Engineer agent's history and its GitHub connection
  ///
  /// In en, this message translates to:
  /// **'Engineer'**
  String get settingsSectionEngineer;

  /// You screen section label: AI engine status and on-device usage
  ///
  /// In en, this message translates to:
  /// **'System'**
  String get settingsSectionSystem;

  /// You screen section label
  ///
  /// In en, this message translates to:
  /// **'About'**
  String get settingsSectionAbout;

  /// You screen row title
  ///
  /// In en, this message translates to:
  /// **'Profile'**
  String get settingsProfileTitle;

  /// You screen row subtitle
  ///
  /// In en, this message translates to:
  /// **'Signed-in account and security'**
  String get settingsProfileSubtitle;

  /// You screen row subtitle
  ///
  /// In en, this message translates to:
  /// **'System, light, or dark'**
  String get settingsAppearanceSubtitle;

  /// Server environment screen title and You row title
  ///
  /// In en, this message translates to:
  /// **'Server environment'**
  String get serverEnvironmentTitle;

  /// You screen row subtitle
  ///
  /// In en, this message translates to:
  /// **'Production or development backend'**
  String get settingsServerEnvironmentSubtitle;

  /// You screen row subtitle for the desktop devices page
  ///
  /// In en, this message translates to:
  /// **'Desktop sign-ins and paired Engineer daemons'**
  String get settingsDevicesSubtitle;

  /// Desktop devices screen title and You row title
  ///
  /// In en, this message translates to:
  /// **'Desktop devices'**
  String get devicesTitle;

  /// Desktop devices screen introduction
  ///
  /// In en, this message translates to:
  /// **'Every desktop app signed in to your account and every paired Engineer daemon. Revoke one to sign it out at once.'**
  String get devicesIntro;

  /// Desktop devices empty state title
  ///
  /// In en, this message translates to:
  /// **'No desktop devices'**
  String get devicesEmpty;

  /// Desktop devices empty state message
  ///
  /// In en, this message translates to:
  /// **'Sign in on the desktop app or pair an Engineer daemon and it will appear here.'**
  String get devicesEmptyMessage;

  /// Desktop devices load error title
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t load your devices'**
  String get devicesLoadFailed;

  /// Desktop devices retry button
  ///
  /// In en, this message translates to:
  /// **'Try again'**
  String get devicesRetry;

  /// Fallback device title when the device reported no name
  ///
  /// In en, this message translates to:
  /// **'Unnamed device'**
  String get devicesUnnamed;

  /// Device kind label
  ///
  /// In en, this message translates to:
  /// **'Desktop app'**
  String get devicesKindApp;

  /// Device kind label
  ///
  /// In en, this message translates to:
  /// **'Engineer daemon'**
  String get devicesKindDaemon;

  /// Device kind label for a device registered before kinds were recorded
  ///
  /// In en, this message translates to:
  /// **'Device'**
  String get devicesKindUnknown;

  /// When the device was signed in or paired
  ///
  /// In en, this message translates to:
  /// **'since {date}'**
  String devicesPairedOn(String date);

  /// Marks the device the current session belongs to
  ///
  /// In en, this message translates to:
  /// **'this device'**
  String get devicesThisDevice;

  /// Revoke device button
  ///
  /// In en, this message translates to:
  /// **'Revoke'**
  String get devicesRevoke;

  /// Cancel button in the revoke confirmation
  ///
  /// In en, this message translates to:
  /// **'Keep'**
  String get devicesKeep;

  /// Revoke confirmation dialog title
  ///
  /// In en, this message translates to:
  /// **'Revoke this device?'**
  String get devicesRevokeConfirmTitle;

  /// Revoke confirmation dialog message
  ///
  /// In en, this message translates to:
  /// **'{name} will be signed out immediately and will have to sign in or pair again.'**
  String devicesRevokeConfirmMessage(String name);

  /// Snackbar after a successful revoke
  ///
  /// In en, this message translates to:
  /// **'Device revoked.'**
  String get devicesRevoked;

  /// Snackbar after a failed revoke
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t revoke that device. Try again.'**
  String get devicesRevokeFailed;

  /// You screen row subtitle
  ///
  /// In en, this message translates to:
  /// **'Documents Copilot can search'**
  String get settingsKnowledgeSubtitle;

  /// You screen row subtitle
  ///
  /// In en, this message translates to:
  /// **'Typed facts AURA remembers'**
  String get settingsMemorySubtitle;

  /// You screen row subtitle
  ///
  /// In en, this message translates to:
  /// **'Corrections you’ve sent'**
  String get settingsFeedbackSubtitle;

  /// You screen row title
  ///
  /// In en, this message translates to:
  /// **'Engineer'**
  String get settingsEngineerTitle;

  /// You screen row subtitle
  ///
  /// In en, this message translates to:
  /// **'Workspaces, PRs, and sandbox runs'**
  String get settingsEngineerSubtitle;

  /// You screen row title
  ///
  /// In en, this message translates to:
  /// **'GitHub Connection'**
  String get settingsGithubTitle;

  /// You screen row subtitle
  ///
  /// In en, this message translates to:
  /// **'Connect your own GitHub account'**
  String get settingsGithubSubtitle;

  /// You screen row subtitle
  ///
  /// In en, this message translates to:
  /// **'Whether AURA’s server is reachable'**
  String get settingsEngineStatusSubtitle;

  /// You screen row title
  ///
  /// In en, this message translates to:
  /// **'Automations'**
  String get settingsAutomationsTitle;

  /// You screen row subtitle
  ///
  /// In en, this message translates to:
  /// **'Gmail, GitHub, and Drive helpers'**
  String get settingsAutomationsSubtitle;

  /// You screen row title
  ///
  /// In en, this message translates to:
  /// **'Usage on this device'**
  String get settingsUsageTitle;

  /// You screen row subtitle
  ///
  /// In en, this message translates to:
  /// **'Local latency and request counts'**
  String get settingsUsageSubtitle;

  /// Desktop IDE toggle title
  ///
  /// In en, this message translates to:
  /// **'Desktop IDE'**
  String get settingsDesktopIdeTitle;

  /// Desktop IDE toggle subtitle when on
  ///
  /// In en, this message translates to:
  /// **'AURA opens the IDE shell on this machine'**
  String get settingsDesktopIdeOn;

  /// Desktop IDE toggle subtitle when off
  ///
  /// In en, this message translates to:
  /// **'AURA opens the regular Home screen on this machine'**
  String get settingsDesktopIdeOff;

  /// About card title
  ///
  /// In en, this message translates to:
  /// **'Where AURA runs'**
  String get settingsAboutTitle;

  /// About card body
  ///
  /// In en, this message translates to:
  /// **'Requests are processed on AURA’s servers, never on this device. Your documents, memories, files and tool results are only read by AI models AURA runs itself. If the server’s operator turns it on, your other messages may be answered by an approved hosted AI provider.'**
  String get settingsAboutBody;

  /// Theme mode option
  ///
  /// In en, this message translates to:
  /// **'System'**
  String get themeSystem;

  /// Theme mode option
  ///
  /// In en, this message translates to:
  /// **'Light'**
  String get themeLight;

  /// Theme mode option
  ///
  /// In en, this message translates to:
  /// **'Dark'**
  String get themeDark;

  /// Theme mode option subtitle
  ///
  /// In en, this message translates to:
  /// **'Match the device setting'**
  String get themeSystemSubtitle;

  /// Theme mode option subtitle
  ///
  /// In en, this message translates to:
  /// **'Always light'**
  String get themeLightSubtitle;

  /// Theme mode option subtitle
  ///
  /// In en, this message translates to:
  /// **'Always dark'**
  String get themeDarkSubtitle;

  /// Snackbar after switching backend environment
  ///
  /// In en, this message translates to:
  /// **'Sign in again to continue.'**
  String get serverSwitchedSignInAgain;

  /// Server environment card title
  ///
  /// In en, this message translates to:
  /// **'Current server'**
  String get serverCurrent;

  /// Backend reachability status
  ///
  /// In en, this message translates to:
  /// **'Reachable'**
  String get serverReachable;

  /// Backend reachability status
  ///
  /// In en, this message translates to:
  /// **'Unreachable'**
  String get serverUnreachable;

  /// Backend reachability status while probing
  ///
  /// In en, this message translates to:
  /// **'Checking…'**
  String get serverChecking;

  /// Backend reachability probe failed
  ///
  /// In en, this message translates to:
  /// **'Could not check reachability'**
  String get serverReachabilityError;

  /// Settings row that permanently deletes the user's account.
  ///
  /// In en, this message translates to:
  /// **'Delete account'**
  String get deleteAccountTitle;

  /// Subtitle of the Delete account settings row.
  ///
  /// In en, this message translates to:
  /// **'Permanently erase your account and its data'**
  String get deleteAccountSubtitle;

  /// Title of the account deletion confirmation.
  ///
  /// In en, this message translates to:
  /// **'Delete your account?'**
  String get deleteAccountConfirmTitle;

  /// Body of the account deletion confirmation.
  ///
  /// In en, this message translates to:
  /// **'This permanently erases your AURA account from the server: memories, documents, tasks and other synced records, conversations, and every signed-in device. It cannot be undone. AURA then signs you out and clears its data from this device. To leave without deleting anything, sign out instead.'**
  String get deleteAccountConfirmBody;

  /// Destructive confirm button of the account deletion confirmation.
  ///
  /// In en, this message translates to:
  /// **'Delete'**
  String get deleteAccountConfirm;

  /// Shown after the server confirms the account was erased.
  ///
  /// In en, this message translates to:
  /// **'Your account has been deleted.'**
  String get deleteAccountDone;

  /// Shown when the server revoked sessions but reported a store it could not erase.
  ///
  /// In en, this message translates to:
  /// **'Your sessions were ended, but part of your account could not be erased yet. Sign in and choose Delete account again to finish.'**
  String get deleteAccountPartial;

  /// Prefix of the error shown when the delete request failed; followed by the reason.
  ///
  /// In en, this message translates to:
  /// **'Your account was not deleted.'**
  String get deleteAccountFailed;

  /// Title of the confirmation shown before switching backend server while signed in.
  ///
  /// In en, this message translates to:
  /// **'Switch server?'**
  String get serverSwitchConfirmTitle;

  /// Body of the server-switch confirmation.
  ///
  /// In en, this message translates to:
  /// **'Your account and data live on the server you are leaving. AURA uploads unsynced changes there first when it can, signs you out, and clears its data from this device. Sign in again on the new server.'**
  String get serverSwitchConfirmBody;

  /// Confirm button of the server-switch confirmation.
  ///
  /// In en, this message translates to:
  /// **'Switch'**
  String get serverSwitchConfirm;

  /// Footnote on the server environment screen
  ///
  /// In en, this message translates to:
  /// **'Switching servers clears your session on this device. Sign in again after changing environments.'**
  String get serverSwitchExplainer;

  /// Product name shown on the sign-in screen
  ///
  /// In en, this message translates to:
  /// **'AURA AI'**
  String get appName;

  /// Generic cancel action
  ///
  /// In en, this message translates to:
  /// **'Cancel'**
  String get cancel;

  /// Sign-out action
  ///
  /// In en, this message translates to:
  /// **'Sign out'**
  String get signOut;

  /// Sign-out confirmation dialog title
  ///
  /// In en, this message translates to:
  /// **'Sign out?'**
  String get signOutConfirmTitle;

  /// Sign-out confirmation dialog body
  ///
  /// In en, this message translates to:
  /// **'AURA uploads unsynced changes first when it can reach the server, then clears its data from this device. Changes it cannot upload are lost.'**
  String get signOutConfirmBody;

  /// OS biometric prompt reason
  ///
  /// In en, this message translates to:
  /// **'Authenticate to unlock your AURA AI profile'**
  String get loginBiometricReason;

  /// Sign-in screen tagline
  ///
  /// In en, this message translates to:
  /// **'Personal AI Operating System'**
  String get loginTagline;

  /// Sign-in card title
  ///
  /// In en, this message translates to:
  /// **'Welcome back'**
  String get loginWelcomeBack;

  /// Sign-in card subtitle
  ///
  /// In en, this message translates to:
  /// **'Sign in to sync your schedules, studies, workspace, and agents.'**
  String get loginSubtitle;

  /// Banner shown after the session expired
  ///
  /// In en, this message translates to:
  /// **'Your session expired. Sign in again to pick up where you left off — everything on this device is still here.'**
  String get loginSessionExpired;

  /// Native Google sign-in button
  ///
  /// In en, this message translates to:
  /// **'Sign in with Google'**
  String get loginGoogle;

  /// Browser-based Google sign-in button (desktop)
  ///
  /// In en, this message translates to:
  /// **'Sign in with Google (browser)'**
  String get loginGoogleBrowser;

  /// Biometric unlock button
  ///
  /// In en, this message translates to:
  /// **'Unlock with biometrics'**
  String get loginUnlockBiometrics;

  /// Debug-only toggle to show the developer sign-in panel
  ///
  /// In en, this message translates to:
  /// **'Developer / offline access'**
  String get loginShowDeveloperPanel;

  /// Debug-only toggle to hide the developer sign-in panel
  ///
  /// In en, this message translates to:
  /// **'Hide developer panel'**
  String get loginHideDeveloperPanel;

  /// Debug-only developer sign-in field label
  ///
  /// In en, this message translates to:
  /// **'Developer name'**
  String get loginDeveloperName;

  /// Debug-only developer sign-in field hint
  ///
  /// In en, this message translates to:
  /// **'Enter name'**
  String get loginDeveloperNameHint;

  /// Debug-only developer sign-in field label
  ///
  /// In en, this message translates to:
  /// **'Developer email'**
  String get loginDeveloperEmail;

  /// Debug-only developer sign-in field hint
  ///
  /// In en, this message translates to:
  /// **'Enter email'**
  String get loginDeveloperEmailHint;

  /// Debug-only developer sign-in button
  ///
  /// In en, this message translates to:
  /// **'Access developer environment'**
  String get loginDeveloperAccess;

  /// Profile screen title
  ///
  /// In en, this message translates to:
  /// **'User Profile & Security'**
  String get profileTitle;

  /// Profile screen when no user is signed in
  ///
  /// In en, this message translates to:
  /// **'Not signed in'**
  String get profileNotSignedIn;

  /// Button that reveals device attestation details
  ///
  /// In en, this message translates to:
  /// **'Show Attestation Status'**
  String get profileShowAttestation;

  /// Generic add action
  ///
  /// In en, this message translates to:
  /// **'Add'**
  String get add;

  /// Generic close action / tooltip
  ///
  /// In en, this message translates to:
  /// **'Close'**
  String get close;

  /// Generic retry action
  ///
  /// In en, this message translates to:
  /// **'Retry'**
  String get retry;

  /// Overflow menu button tooltip
  ///
  /// In en, this message translates to:
  /// **'More'**
  String get moreOptions;

  /// Chat: loads older messages
  ///
  /// In en, this message translates to:
  /// **'Load earlier messages'**
  String get chatLoadEarlier;

  /// Quick capture sheet title
  ///
  /// In en, this message translates to:
  /// **'Ask Copilot'**
  String get captureTitle;

  /// Quick capture field hint
  ///
  /// In en, this message translates to:
  /// **'Capture a thought, task, or question…'**
  String get captureHint;

  /// Quick capture send button
  ///
  /// In en, this message translates to:
  /// **'Send to Copilot'**
  String get captureSend;

  /// Composer chip: use knowledge retrieval
  ///
  /// In en, this message translates to:
  /// **'Knowledge'**
  String get chatToggleKnowledge;

  /// Composer chip: use memory
  ///
  /// In en, this message translates to:
  /// **'Memory'**
  String get chatToggleMemory;

  /// Composer chip: allow tools
  ///
  /// In en, this message translates to:
  /// **'Tools'**
  String get chatToggleTools;

  /// Composer hint while a reply is pending
  ///
  /// In en, this message translates to:
  /// **'Waiting for {persona}...'**
  String chatComposerWaiting(String persona);

  /// Composer hint on narrow screens
  ///
  /// In en, this message translates to:
  /// **'Ask {persona}...'**
  String chatComposerHintShort(String persona);

  /// Composer hint on wide screens
  ///
  /// In en, this message translates to:
  /// **'Ask {persona} anything...'**
  String chatComposerHint(String persona);

  /// Composer Stop button tooltip/label (mirrors kStopGeneratingLabel)
  ///
  /// In en, this message translates to:
  /// **'Stop generating'**
  String get chatStopGenerating;

  /// Composer Send button tooltip/label
  ///
  /// In en, this message translates to:
  /// **'Send'**
  String get chatSend;

  /// Chat header menu button tooltip (opens the session drawer)
  ///
  /// In en, this message translates to:
  /// **'Chat sessions'**
  String get chatOpenSessions;

  /// Chat header: opens live voice
  ///
  /// In en, this message translates to:
  /// **'Live voice'**
  String get chatLiveVoice;

  /// Attach image button tooltip when full
  ///
  /// In en, this message translates to:
  /// **'Image limit reached'**
  String get chatImageLimitReached;

  /// Attach image button tooltip
  ///
  /// In en, this message translates to:
  /// **'Attach image'**
  String get chatAttachImage;

  /// Screen-reader label of a pending image
  ///
  /// In en, this message translates to:
  /// **'Attached image {index} of {count}: {name}'**
  String chatAttachedImageLabel(int index, int count, String name);

  /// Tooltip of the remove-image button
  ///
  /// In en, this message translates to:
  /// **'Remove image {index}'**
  String chatRemoveImage(int index);

  /// Screen-reader label of a sent image
  ///
  /// In en, this message translates to:
  /// **'Sent image {index} of {count}: {name}. Open preview'**
  String chatSentImageLabel(int index, int count, String name);

  /// Image preview close tooltip
  ///
  /// In en, this message translates to:
  /// **'Close preview'**
  String get chatClosePreview;

  /// Chat sidebar: start a new session
  ///
  /// In en, this message translates to:
  /// **'New Session'**
  String get chatNewSession;

  /// Chat sidebar section header
  ///
  /// In en, this message translates to:
  /// **'CHAT SESSIONS'**
  String get chatSessionsHeader;

  /// Name of the default chat session
  ///
  /// In en, this message translates to:
  /// **'Default Workspace'**
  String get chatDefaultSession;

  /// Name of a non-default chat session
  ///
  /// In en, this message translates to:
  /// **'Session {id}'**
  String chatSessionLabel(String id);

  /// Delete conversation tooltip
  ///
  /// In en, this message translates to:
  /// **'Delete conversation'**
  String get chatDeleteConversation;

  /// Snackbar after deleting a conversation
  ///
  /// In en, this message translates to:
  /// **'Conversation deleted'**
  String get chatConversationDeleted;

  /// Empty-answer card title
  ///
  /// In en, this message translates to:
  /// **'No reply this turn'**
  String get chatNoReplyTitle;

  /// Empty-answer card body
  ///
  /// In en, this message translates to:
  /// **'AURA finished without an answer. Retry to send your last message again.'**
  String get chatNoReplyBody;

  /// Empty chat title
  ///
  /// In en, this message translates to:
  /// **'Ask Copilot Anything'**
  String get chatEmptyTitle;

  /// Empty chat message
  ///
  /// In en, this message translates to:
  /// **'Programming questions, academic inquiries, or coordinating your tasks.'**
  String get chatEmptyMessage;

  /// Empty chat suggestion title
  ///
  /// In en, this message translates to:
  /// **'Explain a Concept'**
  String get chatSuggestExplainTitle;

  /// Empty chat suggestion subtitle
  ///
  /// In en, this message translates to:
  /// **'Ask a programming or academic question'**
  String get chatSuggestExplainSubtitle;

  /// Prompt sent when the suggestion is tapped
  ///
  /// In en, this message translates to:
  /// **'Explain how dependency injection works'**
  String get chatSuggestExplainPrompt;

  /// Empty chat suggestion title
  ///
  /// In en, this message translates to:
  /// **'Log Health'**
  String get chatSuggestHealthTitle;

  /// Empty chat suggestion subtitle
  ///
  /// In en, this message translates to:
  /// **'Log water intake and exercise duration for today'**
  String get chatSuggestHealthSubtitle;

  /// Prompt sent when the suggestion is tapped
  ///
  /// In en, this message translates to:
  /// **'Log 500ml of water and 30 minutes of exercise'**
  String get chatSuggestHealthPrompt;

  /// Empty chat suggestion title
  ///
  /// In en, this message translates to:
  /// **'Financial Health'**
  String get chatSuggestFinanceTitle;

  /// Empty chat suggestion subtitle
  ///
  /// In en, this message translates to:
  /// **'Show my current balance and expense distribution'**
  String get chatSuggestFinanceSubtitle;

  /// Prompt sent when the suggestion is tapped
  ///
  /// In en, this message translates to:
  /// **'How are my expenses this month?'**
  String get chatSuggestFinancePrompt;

  /// Empty chat suggestion title
  ///
  /// In en, this message translates to:
  /// **'Agent Memory'**
  String get chatSuggestMemoryTitle;

  /// Empty chat suggestion subtitle
  ///
  /// In en, this message translates to:
  /// **'View facts the system has remembered about me'**
  String get chatSuggestMemorySubtitle;

  /// Prompt sent when the suggestion is tapped
  ///
  /// In en, this message translates to:
  /// **'What do you remember about me?'**
  String get chatSuggestMemoryPrompt;

  /// Feedback sheet title and rate button tooltip
  ///
  /// In en, this message translates to:
  /// **'Rate this answer'**
  String get chatRateAnswer;

  /// Star rating button tooltip
  ///
  /// In en, this message translates to:
  /// **'{value} of {max}'**
  String chatRatingStar(int value, int max);

  /// Feedback sheet correction field label
  ///
  /// In en, this message translates to:
  /// **'Optional correction'**
  String get chatFeedbackCorrection;

  /// Feedback sheet submit button
  ///
  /// In en, this message translates to:
  /// **'Send feedback'**
  String get chatFeedbackSend;

  /// Snackbar after sending feedback
  ///
  /// In en, this message translates to:
  /// **'Thanks — feedback sent.'**
  String get chatFeedbackSent;

  /// Memory bank dialog title
  ///
  /// In en, this message translates to:
  /// **'User Memory Bank'**
  String get memoryBankTitle;

  /// Memory bank dialog explainer
  ///
  /// In en, this message translates to:
  /// **'These are facts and preferences AURA has learned about you. They are injected as system prompts to personalize reasoning.'**
  String get memoryBankExplainer;

  /// Memory bank add field hint
  ///
  /// In en, this message translates to:
  /// **'Add a custom fact manually...'**
  String get memoryBankAddHint;

  /// Memory bank empty state
  ///
  /// In en, this message translates to:
  /// **'No memories stored yet. AURA extracts them automatically from conversations, or you can add one above.'**
  String get memoryBankEmpty;

  /// Memory bank delete button tooltip
  ///
  /// In en, this message translates to:
  /// **'Delete memory'**
  String get memoryBankDelete;

  /// Connection error card title
  ///
  /// In en, this message translates to:
  /// **'Cloud AI is currently unavailable'**
  String get chatCloudUnavailableTitle;

  /// Connection error card body
  ///
  /// In en, this message translates to:
  /// **'AURA could not connect to the cloud assistant. You can retry or report this issue.'**
  String get chatCloudUnavailableBody;

  /// Connection error card report button
  ///
  /// In en, this message translates to:
  /// **'Report Issue'**
  String get chatReportIssue;

  /// Pending actions card title
  ///
  /// In en, this message translates to:
  /// **'AURA Proposes Actions'**
  String get pendingActionsTitle;

  /// Pending actions reject button
  ///
  /// In en, this message translates to:
  /// **'Reject'**
  String get pendingActionsReject;

  /// Pending actions approve button
  ///
  /// In en, this message translates to:
  /// **'Approve Actions'**
  String get pendingActionsApprove;

  /// Snackbar when every proposed action ran
  ///
  /// In en, this message translates to:
  /// **'Proposed actions successfully executed!'**
  String get pendingActionsAllSucceeded;

  /// Snackbar when every proposed action failed
  ///
  /// In en, this message translates to:
  /// **'All actions failed: {labels}'**
  String pendingActionsAllFailed(String labels);

  /// Snackbar when some proposed actions failed
  ///
  /// In en, this message translates to:
  /// **'{count} action(s) failed: {labels}'**
  String pendingActionsSomeFailed(int count, String labels);

  /// Voice note error
  ///
  /// In en, this message translates to:
  /// **'Microphone permission is needed to record a voice note.'**
  String get voiceMicPermission;

  /// Voice note error
  ///
  /// In en, this message translates to:
  /// **'Voice recording is not available on this device.'**
  String get voiceRecordingUnavailable;

  /// Voice note error
  ///
  /// In en, this message translates to:
  /// **'Could not transcribe that voice note.'**
  String get voiceTranscribeFailed;

  /// Voice note error
  ///
  /// In en, this message translates to:
  /// **'Could not read that recording.'**
  String get voiceReadFailed;

  /// Voice note button tooltip while recording
  ///
  /// In en, this message translates to:
  /// **'Stop recording'**
  String get voiceStopRecording;

  /// Voice note button tooltip
  ///
  /// In en, this message translates to:
  /// **'Record a voice note'**
  String get voiceRecordNote;

  /// Reply playback button tooltip while playing
  ///
  /// In en, this message translates to:
  /// **'Stop'**
  String get voiceStopPlayback;

  /// Reply playback button tooltip
  ///
  /// In en, this message translates to:
  /// **'Listen to this reply'**
  String get voiceListen;

  /// Persona picker: Engineer history button tooltip
  ///
  /// In en, this message translates to:
  /// **'Engineer Task History'**
  String get engineerTaskHistory;

  /// Sandbox diff view when the diff is empty
  ///
  /// In en, this message translates to:
  /// **'(no changes)'**
  String get sandboxNoChanges;

  /// Screen-reader prefix of an error state view
  ///
  /// In en, this message translates to:
  /// **'Error'**
  String get stateToneError;

  /// Screen-reader prefix of a warning state view
  ///
  /// In en, this message translates to:
  /// **'Warning'**
  String get stateToneWarning;

  /// Screen-reader prefix of an offline state view
  ///
  /// In en, this message translates to:
  /// **'Offline'**
  String get stateToneOffline;

  /// Command palette barrier label
  ///
  /// In en, this message translates to:
  /// **'Command palette'**
  String get commandPaletteLabel;

  /// Command palette search hint
  ///
  /// In en, this message translates to:
  /// **'Type a command...'**
  String get commandPaletteHint;

  /// Command palette empty result title
  ///
  /// In en, this message translates to:
  /// **'No matching commands'**
  String get commandPaletteNoMatchesTitle;

  /// Command palette empty result body
  ///
  /// In en, this message translates to:
  /// **'Nothing here matches \"{query}\". Try a shorter word, or clear the search to see everything.'**
  String commandPaletteNoMatchesBody(String query);

  /// Command palette clear-search action
  ///
  /// In en, this message translates to:
  /// **'Clear search'**
  String get commandPaletteClearSearch;

  /// Generic refresh tooltip
  ///
  /// In en, this message translates to:
  /// **'Refresh'**
  String get refresh;

  /// Tooltip of a date-picker button
  ///
  /// In en, this message translates to:
  /// **'Change date'**
  String get changeDate;

  /// Tooltip of the button that removes a task due date
  ///
  /// In en, this message translates to:
  /// **'Clear due date'**
  String get clearDueDate;

  /// Task completion toggle tooltip
  ///
  /// In en, this message translates to:
  /// **'Mark as done'**
  String get taskMarkDone;

  /// Task completion toggle tooltip when already completed
  ///
  /// In en, this message translates to:
  /// **'Mark as not done'**
  String get taskMarkNotDone;

  /// Host daemon page: file browse button tooltip
  ///
  /// In en, this message translates to:
  /// **'Browse for the daemon executable'**
  String get hostdBrowseExecutable;

  /// Host daemon page: save path button tooltip
  ///
  /// In en, this message translates to:
  /// **'Save executable path'**
  String get hostdSaveExecutable;

  /// Live voice page close button tooltip
  ///
  /// In en, this message translates to:
  /// **'End live voice'**
  String get liveVoiceEnd;

  /// Add event dialog: start time picker tooltip
  ///
  /// In en, this message translates to:
  /// **'Change start time'**
  String get eventChangeStart;

  /// Add event dialog: end time picker tooltip
  ///
  /// In en, this message translates to:
  /// **'Change end time'**
  String get eventChangeEnd;

  /// Calendar: add event button tooltip
  ///
  /// In en, this message translates to:
  /// **'Add event'**
  String get eventAdd;

  /// Calendar: delete event button tooltip
  ///
  /// In en, this message translates to:
  /// **'Delete event'**
  String get eventDelete;

  /// Calendar month navigation tooltip
  ///
  /// In en, this message translates to:
  /// **'Previous month'**
  String get calendarPreviousMonth;

  /// Calendar month navigation tooltip
  ///
  /// In en, this message translates to:
  /// **'Next month'**
  String get calendarNextMonth;

  /// GitHub automation: import subtask button tooltip
  ///
  /// In en, this message translates to:
  /// **'Import as coding task'**
  String get githubImportSubtask;

  /// Chat sidebar section header
  ///
  /// In en, this message translates to:
  /// **'ACTIVE PERSONA'**
  String get chatActivePersonaHeader;

  /// Chat sidebar knowledge toggle
  ///
  /// In en, this message translates to:
  /// **'Use Knowledge (RAG)'**
  String get chatUseKnowledge;

  /// Chat sidebar section header
  ///
  /// In en, this message translates to:
  /// **'AI ROUTING'**
  String get chatRoutingHeader;

  /// Chat sidebar routing row label
  ///
  /// In en, this message translates to:
  /// **'AI engine'**
  String get chatAiEngine;

  /// Snackbar after reporting a connection issue
  ///
  /// In en, this message translates to:
  /// **'Issue recorded in local telemetry.'**
  String get chatIssueRecorded;

  /// Notice when the backend served a different persona than requested
  ///
  /// In en, this message translates to:
  /// **'This request was handled by {resolved}, not {requested} -- your account may not have access to {requested}.'**
  String chatPersonaFallback(String resolved, String requested);

  /// Hint when a turn waits on a tool confirmation
  ///
  /// In en, this message translates to:
  /// **'Confirm the action above to continue.'**
  String get chatAwaitingConfirmation;

  /// Persona picker sheet title
  ///
  /// In en, this message translates to:
  /// **'Choose a persona'**
  String get personaPickerTitle;

  /// Debug-only hint after a failed Google sign-in
  ///
  /// In en, this message translates to:
  /// **'Tip: Google Sign-In failed. To test locally without registering SHA-1 fingerprints in the Google Console, use the \"Access Developer Environment\" bypass option above.'**
  String get loginDeveloperTip;

  /// Shown under an answer when the turn planned steps that did not finish
  ///
  /// In en, this message translates to:
  /// **'{gaps, plural, =1{AURA planned {total} steps and one did not complete.} other{AURA planned {total} steps and {gaps} did not complete.}}'**
  String planIncompleteHeadline(int total, int gaps);

  /// Second sentence of the screen-reader announcement for an incomplete plan
  ///
  /// In en, this message translates to:
  /// **'This answer was given without them.'**
  String get planIncompleteAnnouncement;

  /// One unfinished plan step and the backend's own status word
  ///
  /// In en, this message translates to:
  /// **'{step} — {status}'**
  String planStepWithStatus(String step, String status);

  /// Appearance page: heading above the language choices
  ///
  /// In en, this message translates to:
  /// **'Language'**
  String get languageSectionTitle;

  /// Language option: follow the device language
  ///
  /// In en, this message translates to:
  /// **'System'**
  String get languageSystem;

  /// Subtitle under the System language option
  ///
  /// In en, this message translates to:
  /// **'Use your device\'s language'**
  String get languageSystemSubtitle;

  /// Persistent banner after device attestation was rejected
  ///
  /// In en, this message translates to:
  /// **'This device could not be verified. Some features may be unavailable.'**
  String get deviceUnverifiedBanner;

  /// Generic button: close a banner or notice
  ///
  /// In en, this message translates to:
  /// **'Dismiss'**
  String get commonDismiss;

  /// Generic button: save changes
  ///
  /// In en, this message translates to:
  /// **'Save'**
  String get commonSave;

  /// Generic button: delete an item
  ///
  /// In en, this message translates to:
  /// **'Delete'**
  String get commonDelete;

  /// Generic button: edit an item
  ///
  /// In en, this message translates to:
  /// **'Edit'**
  String get commonEdit;

  /// Generic button: add an item
  ///
  /// In en, this message translates to:
  /// **'Add'**
  String get commonAdd;

  /// Generic button: acknowledge
  ///
  /// In en, this message translates to:
  /// **'OK'**
  String get commonOk;

  /// Generic button: confirm an action
  ///
  /// In en, this message translates to:
  /// **'Confirm'**
  String get commonConfirm;

  /// Generic snackbar action: undo the last change
  ///
  /// In en, this message translates to:
  /// **'Undo'**
  String get commonUndo;

  /// Generic button/tooltip: go back
  ///
  /// In en, this message translates to:
  /// **'Back'**
  String get commonBack;

  /// Generic button: next step
  ///
  /// In en, this message translates to:
  /// **'Next'**
  String get commonNext;

  /// Generic button: finish
  ///
  /// In en, this message translates to:
  /// **'Done'**
  String get commonDone;

  /// Generic button/tooltip: copy to clipboard
  ///
  /// In en, this message translates to:
  /// **'Copy'**
  String get commonCopy;

  /// Snackbar after copying to clipboard
  ///
  /// In en, this message translates to:
  /// **'Copied'**
  String get commonCopied;

  /// Generic button/tooltip: reload
  ///
  /// In en, this message translates to:
  /// **'Refresh'**
  String get commonRefresh;

  /// Generic search field hint or tooltip
  ///
  /// In en, this message translates to:
  /// **'Search'**
  String get commonSearch;

  /// Generic loading label
  ///
  /// In en, this message translates to:
  /// **'Loading…'**
  String get commonLoading;

  /// Generic error title
  ///
  /// In en, this message translates to:
  /// **'Something went wrong'**
  String get commonSomethingWentWrong;

  /// Shown when a value is empty
  ///
  /// In en, this message translates to:
  /// **'None'**
  String get commonNone;

  /// Generic answer
  ///
  /// In en, this message translates to:
  /// **'Yes'**
  String get commonYes;

  /// Generic answer
  ///
  /// In en, this message translates to:
  /// **'No'**
  String get commonNo;

  /// Health page: title
  ///
  /// In en, this message translates to:
  /// **'Health Monitor'**
  String get healthPageTitle;

  /// Health page: subtitle under the title
  ///
  /// In en, this message translates to:
  /// **'Track fitness parameters, log water, sleep, and review logs'**
  String get healthPageSubtitle;

  /// Health dashboard: daily goals card heading
  ///
  /// In en, this message translates to:
  /// **'Daily Goals Progress'**
  String get healthDailyGoalsTitle;

  /// Health dashboard: badge with today's logged weight
  ///
  /// In en, this message translates to:
  /// **'Weight: {weight} kg'**
  String healthWeightBadge(String weight);

  /// Health dashboard: water progress bar title
  ///
  /// In en, this message translates to:
  /// **'Water Intake'**
  String get healthWaterIntake;

  /// Health dashboard: millilitre unit after water amounts
  ///
  /// In en, this message translates to:
  /// **'ml'**
  String get healthUnitMl;

  /// Health dashboard: sleep progress bar title
  ///
  /// In en, this message translates to:
  /// **'Sleep Duration'**
  String get healthSleepDuration;

  /// Health dashboard: minutes unit after sleep/exercise amounts
  ///
  /// In en, this message translates to:
  /// **'mins'**
  String get healthUnitMins;

  /// Health dashboard: sleep hours logged out of the 8-hour goal
  ///
  /// In en, this message translates to:
  /// **'{hours} / 8.0 hrs'**
  String healthSleepHoursProgress(String hours);

  /// Health dashboard: exercise progress bar title
  ///
  /// In en, this message translates to:
  /// **'Exercise Duration'**
  String get healthExerciseDuration;

  /// Health dashboard: calories progress bar title
  ///
  /// In en, this message translates to:
  /// **'Calories Consumed'**
  String get healthCaloriesConsumed;

  /// Health dashboard: kilocalorie unit after calorie amounts
  ///
  /// In en, this message translates to:
  /// **'kcal'**
  String get healthUnitKcal;

  /// Health dashboard: quick logging card heading
  ///
  /// In en, this message translates to:
  /// **'Quick Logger Panel'**
  String get healthQuickLoggerTitle;

  /// Health dashboard: button that logs 250 ml of water
  ///
  /// In en, this message translates to:
  /// **'+250ml Water'**
  String get healthAddWater250;

  /// Health dashboard: button that logs 500 ml of water
  ///
  /// In en, this message translates to:
  /// **'+500ml Water'**
  String get healthAddWater500;

  /// Health log dialog title: sleep
  ///
  /// In en, this message translates to:
  /// **'Log Sleep Duration'**
  String get healthLogSleepTitle;

  /// Health log dialog: sleep minutes field label
  ///
  /// In en, this message translates to:
  /// **'Sleep Minutes'**
  String get healthSleepMinutesLabel;

  /// Health log dialog: sleep minutes field hint
  ///
  /// In en, this message translates to:
  /// **'e.g. 480 for 8 hours'**
  String get healthSleepMinutesHint;

  /// Health dashboard: button opening the sleep log dialog
  ///
  /// In en, this message translates to:
  /// **'Log Sleep'**
  String get healthLogSleep;

  /// Health log dialog title: exercise
  ///
  /// In en, this message translates to:
  /// **'Log Exercise Duration'**
  String get healthLogExerciseTitle;

  /// Health log dialog: exercise minutes field label
  ///
  /// In en, this message translates to:
  /// **'Exercise Minutes'**
  String get healthExerciseMinutesLabel;

  /// Health log dialog: example value hint in a numeric field
  ///
  /// In en, this message translates to:
  /// **'e.g. {example}'**
  String healthExampleHint(String example);

  /// Health dashboard: button opening the exercise log dialog
  ///
  /// In en, this message translates to:
  /// **'Log Exercise'**
  String get healthLogExercise;

  /// Health log dialog title: calories
  ///
  /// In en, this message translates to:
  /// **'Log Calories'**
  String get healthLogCaloriesTitle;

  /// Health log dialog: calories field label
  ///
  /// In en, this message translates to:
  /// **'Kcal Consumed'**
  String get healthKcalConsumedLabel;

  /// Health dashboard: button opening the calories log dialog
  ///
  /// In en, this message translates to:
  /// **'Log Calories'**
  String get healthLogCalories;

  /// Health log dialog title: weight
  ///
  /// In en, this message translates to:
  /// **'Log Current Weight'**
  String get healthLogWeightTitle;

  /// Health log dialog: weight field label
  ///
  /// In en, this message translates to:
  /// **'Weight (kg)'**
  String get healthWeightKgLabel;

  /// Health dashboard: button opening the weight log dialog
  ///
  /// In en, this message translates to:
  /// **'Log Weight'**
  String get healthLogWeight;

  /// Health trend charts: card heading
  ///
  /// In en, this message translates to:
  /// **'Weekly Health Trends'**
  String get healthWeeklyTrendsTitle;

  /// Health trend charts: empty state
  ///
  /// In en, this message translates to:
  /// **'Log metrics to populate weekly graphs'**
  String get healthWeeklyTrendsEmpty;

  /// Health trend charts: water bar chart caption
  ///
  /// In en, this message translates to:
  /// **'Water Intake (ml)'**
  String get healthWaterChartTitle;

  /// Health trend charts: weight line chart caption
  ///
  /// In en, this message translates to:
  /// **'Weight Trends (kg)'**
  String get healthWeightChartTitle;

  /// Health trend charts: weight chart empty state
  ///
  /// In en, this message translates to:
  /// **'Log weight to display trend line'**
  String get healthWeightChartEmpty;

  /// Knowledge page: snackbar when a picked PDF has no readable bytes
  ///
  /// In en, this message translates to:
  /// **'Could not read that file.'**
  String get knowledgeFileReadFailed;

  /// Knowledge page: heading of the add-document card
  ///
  /// In en, this message translates to:
  /// **'Add a document'**
  String get knowledgeAddDocument;

  /// Knowledge page: document title field label
  ///
  /// In en, this message translates to:
  /// **'Title'**
  String get knowledgeTitleLabel;

  /// Knowledge page: document content field label
  ///
  /// In en, this message translates to:
  /// **'Content'**
  String get knowledgeContentLabel;

  /// Knowledge page: snackbar when saving with an empty title or content
  ///
  /// In en, this message translates to:
  /// **'Title and content are required.'**
  String get knowledgeTitleContentRequired;

  /// Knowledge page: upload button label while a PDF uploads
  ///
  /// In en, this message translates to:
  /// **'Uploading…'**
  String get knowledgeUploading;

  /// Knowledge page: button to pick and upload a PDF
  ///
  /// In en, this message translates to:
  /// **'Upload a PDF instead'**
  String get knowledgeUploadPdfInstead;

  /// Knowledge page: search field label
  ///
  /// In en, this message translates to:
  /// **'Look up a passage'**
  String get knowledgeSearchLabel;

  /// Knowledge page: search returned no results
  ///
  /// In en, this message translates to:
  /// **'Searched your library — nothing relevant turned up.'**
  String get knowledgeSearchEmpty;

  /// Knowledge page: heading of the pinned-context (CAG) card
  ///
  /// In en, this message translates to:
  /// **'Always in context (CAG)'**
  String get knowledgeCagTitle;

  /// Knowledge page: CAG token budget used out of total
  ///
  /// In en, this message translates to:
  /// **'Budget {tokens} / {budget} tokens'**
  String knowledgeCagBudget(int tokens, int budget);

  /// Knowledge page: appended to the CAG budget line when no document is pinned
  ///
  /// In en, this message translates to:
  /// **'nothing pinned yet'**
  String get knowledgeCagNothingPinned;

  /// Knowledge page: CAG document entry that comes from the shared handbook
  ///
  /// In en, this message translates to:
  /// **'handbook'**
  String get knowledgeCagHandbook;

  /// Knowledge page: CAG document entry that belongs to the user
  ///
  /// In en, this message translates to:
  /// **'yours'**
  String get knowledgeCagYours;

  /// Knowledge page: token count of one pinned CAG document
  ///
  /// In en, this message translates to:
  /// **'{tokens} tok'**
  String knowledgeCagDocTokens(String tokens);

  /// Knowledge page: heading above the document list
  ///
  /// In en, this message translates to:
  /// **'Library'**
  String get knowledgeLibrary;

  /// Knowledge page: error state title when the document list fails
  ///
  /// In en, this message translates to:
  /// **'Could not load documents'**
  String get knowledgeLoadFailed;

  /// Knowledge page: empty library title
  ///
  /// In en, this message translates to:
  /// **'No documents yet'**
  String get knowledgeEmptyTitle;

  /// Knowledge page: empty library message
  ///
  /// In en, this message translates to:
  /// **'Save a title and some text to build your library.'**
  String get knowledgeEmptyMessage;

  /// Knowledge page: document subtitle tag when pinned to CAG
  ///
  /// In en, this message translates to:
  /// **'Always in context'**
  String get knowledgeAlwaysInContext;

  /// Knowledge page: tooltip to unpin a document from CAG
  ///
  /// In en, this message translates to:
  /// **'Remove from always-in-context'**
  String get knowledgeUnpinTooltip;

  /// Knowledge page: tooltip to pin a document to CAG
  ///
  /// In en, this message translates to:
  /// **'Keep always in context'**
  String get knowledgePinTooltip;

  /// Knowledge page: message when ingesting a typed document fails
  ///
  /// In en, this message translates to:
  /// **'Could not add this document.'**
  String get knowledgeAddFailed;

  /// Knowledge page: appended after Saved/Uploaded when some passages were quarantined
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 passage was held back.} other{{count} passages were held back.}}'**
  String knowledgePassagesHeldBack(int count);

  /// Knowledge page: message after a document is saved
  ///
  /// In en, this message translates to:
  /// **'Saved.'**
  String get knowledgeSaved;

  /// Knowledge page: message when a PDF upload fails
  ///
  /// In en, this message translates to:
  /// **'Could not upload this PDF.'**
  String get knowledgePdfUploadFailed;

  /// Knowledge page: message after a PDF is uploaded
  ///
  /// In en, this message translates to:
  /// **'Uploaded.'**
  String get knowledgeUploaded;

  /// Goals page: greeting above the user's name
  ///
  /// In en, this message translates to:
  /// **'Welcome back,'**
  String get goalsWelcomeBack;

  /// Goals page: fallback name when the account has no display name
  ///
  /// In en, this message translates to:
  /// **'Aura User'**
  String get goalsDefaultUserName;

  /// Goals page: title
  ///
  /// In en, this message translates to:
  /// **'Goals'**
  String get goalsPageTitle;

  /// Goals page: subtitle under the title
  ///
  /// In en, this message translates to:
  /// **'Track your aspirations and progress'**
  String get goalsPageSubtitle;

  /// Goals page: button that opens the create-goal dialog
  ///
  /// In en, this message translates to:
  /// **'New Goal'**
  String get goalsNewGoal;

  /// Goals page: empty state title
  ///
  /// In en, this message translates to:
  /// **'No goals yet'**
  String get goalsEmptyTitle;

  /// Goals page: empty state message
  ///
  /// In en, this message translates to:
  /// **'Create your first goal to get started'**
  String get goalsEmptyMessage;

  /// Create-goal dialog: title
  ///
  /// In en, this message translates to:
  /// **'Create Goal'**
  String get goalsCreateGoalTitle;

  /// Create-goal dialog: required title field label
  ///
  /// In en, this message translates to:
  /// **'Title *'**
  String get goalsTitleRequiredLabel;

  /// Create-goal dialog: description field label
  ///
  /// In en, this message translates to:
  /// **'Description'**
  String get goalsDescriptionLabel;

  /// Create-goal dialog: status dropdown label
  ///
  /// In en, this message translates to:
  /// **'Status'**
  String get goalsStatusLabel;

  /// Create-goal dialog: priority dropdown label
  ///
  /// In en, this message translates to:
  /// **'Priority'**
  String get goalsPriorityLabel;

  /// Create-goal dialog: target date row before a date is picked
  ///
  /// In en, this message translates to:
  /// **'Set target date (optional)'**
  String get goalsSetTargetDate;

  /// Goals: target date of a goal (dialog row and goal card)
  ///
  /// In en, this message translates to:
  /// **'Target: {date}'**
  String goalsTargetDate(String date);

  /// Create-goal dialog: progress slider caption
  ///
  /// In en, this message translates to:
  /// **'Progress: {percent}%'**
  String goalsProgressPercent(int percent);

  /// Create-goal dialog: confirm button
  ///
  /// In en, this message translates to:
  /// **'Create'**
  String get goalsCreate;

  /// Goal status value: active (dropdown item and card chip)
  ///
  /// In en, this message translates to:
  /// **'active'**
  String get goalsStatusActive;

  /// Goal status value: achieved (dropdown item and card chip)
  ///
  /// In en, this message translates to:
  /// **'achieved'**
  String get goalsStatusAchieved;

  /// Goal status value: abandoned (dropdown item and card chip)
  ///
  /// In en, this message translates to:
  /// **'abandoned'**
  String get goalsStatusAbandoned;

  /// Goal priority value: low (dropdown item and card chip)
  ///
  /// In en, this message translates to:
  /// **'low'**
  String get goalsPriorityLow;

  /// Goal priority value: medium (dropdown item and card chip)
  ///
  /// In en, this message translates to:
  /// **'medium'**
  String get goalsPriorityMedium;

  /// Goal priority value: high (dropdown item and card chip)
  ///
  /// In en, this message translates to:
  /// **'high'**
  String get goalsPriorityHigh;

  /// Tasks page: greeting above the user's name
  ///
  /// In en, this message translates to:
  /// **'Welcome back,'**
  String get tasksWelcomeBack;

  /// Tasks page: fallback name when the account has no display name
  ///
  /// In en, this message translates to:
  /// **'Aura User'**
  String get tasksDefaultUserName;

  /// Tasks page: title
  ///
  /// In en, this message translates to:
  /// **'Task Workspace'**
  String get tasksPageTitle;

  /// Tasks page: subtitle under the title
  ///
  /// In en, this message translates to:
  /// **'Prioritize and manage your goals'**
  String get tasksPageSubtitle;

  /// Tasks page: tooltip of the button that opens the sync log sheet
  ///
  /// In en, this message translates to:
  /// **'View Sync Logs'**
  String get tasksViewSyncLogs;

  /// Tasks page: shown while Google Tasks sync runs
  ///
  /// In en, this message translates to:
  /// **'Syncing...'**
  String get tasksSyncing;

  /// Tasks page: snackbar after a sync with errors
  ///
  /// In en, this message translates to:
  /// **'Tasks sync encountered errors.'**
  String get tasksSyncFailed;

  /// Tasks page: snackbar after a successful sync
  ///
  /// In en, this message translates to:
  /// **'Google Tasks sync completed successfully!'**
  String get tasksSyncSucceeded;

  /// Tasks page: button that syncs with Google Tasks
  ///
  /// In en, this message translates to:
  /// **'Sync Tasks'**
  String get tasksSyncButton;

  /// Tasks page: button that opens the add-task dialog
  ///
  /// In en, this message translates to:
  /// **'Add Task'**
  String get tasksAddTask;

  /// Add-task dialog: title
  ///
  /// In en, this message translates to:
  /// **'Add New Task'**
  String get tasksAddNewTaskTitle;

  /// Add-task dialog: title field label
  ///
  /// In en, this message translates to:
  /// **'Task Title'**
  String get tasksTitleLabel;

  /// Add-task dialog: title field hint
  ///
  /// In en, this message translates to:
  /// **'What needs to be done?'**
  String get tasksTitleHint;

  /// Add-task dialog: validation error when the title is empty
  ///
  /// In en, this message translates to:
  /// **'Please enter a title'**
  String get tasksTitleRequired;

  /// Add-task dialog: description field label
  ///
  /// In en, this message translates to:
  /// **'Description'**
  String get tasksDescriptionLabel;

  /// Add-task dialog: description field hint
  ///
  /// In en, this message translates to:
  /// **'Add details (optional)'**
  String get tasksDescriptionHint;

  /// Add-task dialog: category dropdown label
  ///
  /// In en, this message translates to:
  /// **'Category'**
  String get tasksCategoryLabel;

  /// Add-task dialog: priority dropdown label
  ///
  /// In en, this message translates to:
  /// **'Priority'**
  String get tasksPriorityLabel;

  /// Add-task dialog: due date row before a date is picked
  ///
  /// In en, this message translates to:
  /// **'No Due Date Selected'**
  String get tasksNoDueDate;

  /// Add-task dialog: confirm button
  ///
  /// In en, this message translates to:
  /// **'Save Task'**
  String get tasksSaveTask;

  /// Task category filter chip: all categories
  ///
  /// In en, this message translates to:
  /// **'All'**
  String get tasksCategoryAll;

  /// Task category: personal (filter chip; upper-cased in the dialog and card)
  ///
  /// In en, this message translates to:
  /// **'Personal'**
  String get tasksCategoryPersonal;

  /// Task category: academic (filter chip; upper-cased in the dialog and card)
  ///
  /// In en, this message translates to:
  /// **'Academic'**
  String get tasksCategoryAcademic;

  /// Task category: client (filter chip; upper-cased in the dialog and card)
  ///
  /// In en, this message translates to:
  /// **'Client'**
  String get tasksCategoryClient;

  /// Task category: project (filter chip; upper-cased in the dialog and card)
  ///
  /// In en, this message translates to:
  /// **'Project'**
  String get tasksCategoryProject;

  /// Task priority: low (upper-cased in the dialog and card)
  ///
  /// In en, this message translates to:
  /// **'Low'**
  String get tasksPriorityLow;

  /// Task priority: medium (upper-cased in the dialog and card)
  ///
  /// In en, this message translates to:
  /// **'Medium'**
  String get tasksPriorityMedium;

  /// Task priority: high (upper-cased in the dialog and card)
  ///
  /// In en, this message translates to:
  /// **'High'**
  String get tasksPriorityHigh;

  /// Tasks page: empty state title
  ///
  /// In en, this message translates to:
  /// **'All caught up!'**
  String get tasksEmptyTitle;

  /// Tasks page: empty state message
  ///
  /// In en, this message translates to:
  /// **'Enjoy your day or create a new task to stay organized.'**
  String get tasksEmptyMessage;

  /// Sync log sheet: title
  ///
  /// In en, this message translates to:
  /// **'Tasks Sync Activity Log'**
  String get tasksSyncLogTitle;

  /// Sync log sheet: empty state
  ///
  /// In en, this message translates to:
  /// **'No logs recorded yet. Press sync to begin.'**
  String get tasksSyncLogEmpty;

  /// Task card: delete button tooltip
  ///
  /// In en, this message translates to:
  /// **'Delete task'**
  String get tasksDeleteTask;

  /// Task card: snackbar after deleting a task (with Undo)
  ///
  /// In en, this message translates to:
  /// **'Task deleted'**
  String get tasksTaskDeleted;

  /// Calendar page: snackbar after a sync with errors
  ///
  /// In en, this message translates to:
  /// **'Calendar sync encountered errors.'**
  String get calendarSyncFailed;

  /// Calendar page: snackbar after a successful sync
  ///
  /// In en, this message translates to:
  /// **'Google Calendar sync completed successfully!'**
  String get calendarSyncSucceeded;

  /// Calendar page: title
  ///
  /// In en, this message translates to:
  /// **'Calendar'**
  String get calendarPageTitle;

  /// Calendar page: subtitle under the title
  ///
  /// In en, this message translates to:
  /// **'Manage local schedules and sync with Google'**
  String get calendarPageSubtitle;

  /// Calendar page: tooltip of the button that opens the sync log sheet
  ///
  /// In en, this message translates to:
  /// **'View Sync Logs'**
  String get calendarViewSyncLogs;

  /// Calendar page: shown while Google Calendar sync runs
  ///
  /// In en, this message translates to:
  /// **'Syncing...'**
  String get calendarSyncing;

  /// Calendar page: button that syncs with Google Calendar
  ///
  /// In en, this message translates to:
  /// **'Sync Calendar'**
  String get calendarSyncButton;

  /// Calendar sync log sheet: title
  ///
  /// In en, this message translates to:
  /// **'Sync Activity Log'**
  String get calendarSyncLogTitle;

  /// Calendar sync log sheet: empty state
  ///
  /// In en, this message translates to:
  /// **'No logs recorded yet. Press sync to begin.'**
  String get calendarSyncLogEmpty;

  /// Add-event dialog: title
  ///
  /// In en, this message translates to:
  /// **'Create Calendar Event'**
  String get calendarCreateEventTitle;

  /// Add-event dialog: title field label
  ///
  /// In en, this message translates to:
  /// **'Event Title'**
  String get calendarEventTitleLabel;

  /// Add-event dialog: title field hint
  ///
  /// In en, this message translates to:
  /// **'e.g. Team Standup'**
  String get calendarEventTitleHint;

  /// Add-event dialog: description field label
  ///
  /// In en, this message translates to:
  /// **'Description (Optional)'**
  String get calendarEventDescriptionLabel;

  /// Add-event dialog: description field hint
  ///
  /// In en, this message translates to:
  /// **'Enter event details'**
  String get calendarEventDescriptionHint;

  /// Add-event dialog: start time row title
  ///
  /// In en, this message translates to:
  /// **'Start Time'**
  String get calendarStartTime;

  /// Add-event dialog: end time row title
  ///
  /// In en, this message translates to:
  /// **'End Time'**
  String get calendarEndTime;

  /// Add-event dialog: snackbar after creating an event
  ///
  /// In en, this message translates to:
  /// **'Event created successfully!'**
  String get calendarEventCreated;

  /// Add-event dialog: confirm button
  ///
  /// In en, this message translates to:
  /// **'Create'**
  String get calendarCreate;

  /// Calendar schedule panel: empty state for the selected day
  ///
  /// In en, this message translates to:
  /// **'No events scheduled for today'**
  String get calendarNoEvents;

  /// Calendar schedule panel: tag on events synced with Google Calendar
  ///
  /// In en, this message translates to:
  /// **'Cloud Synced'**
  String get calendarCloudSynced;

  /// Delete-event confirmation dialog: title
  ///
  /// In en, this message translates to:
  /// **'Delete Event'**
  String get calendarDeleteEventTitle;

  /// Delete-event confirmation dialog: body
  ///
  /// In en, this message translates to:
  /// **'Are you sure you want to delete \'{title}\'?'**
  String calendarDeleteEventConfirm(String title);

  /// Calendar schedule panel: snackbar after deleting an event
  ///
  /// In en, this message translates to:
  /// **'Event deleted successfully.'**
  String get calendarEventDeleted;

  /// IDE Host panel: card title
  ///
  /// In en, this message translates to:
  /// **'Host Daemon'**
  String get hostPanelTitle;

  /// IDE Host panel: button opening the full host daemon settings page
  ///
  /// In en, this message translates to:
  /// **'Full Settings'**
  String get hostFullSettings;

  /// IDE Host panel: status could not be read; error is an already-described message
  ///
  /// In en, this message translates to:
  /// **'Error: {error}'**
  String hostStatusError(String error);

  /// IDE Host panel status: supervisor gave up restarting the daemon
  ///
  /// In en, this message translates to:
  /// **'Stopped after repeated crashes'**
  String get hostStatusGaveUp;

  /// IDE Host panel status: daemon crashed, countdown to automatic restart (seconds)
  ///
  /// In en, this message translates to:
  /// **'Crashed, restarting in {seconds}s...'**
  String hostStatusRestarting(int seconds);

  /// IDE Host panel status: daemon not running
  ///
  /// In en, this message translates to:
  /// **'Not running'**
  String get hostStatusNotRunning;

  /// IDE Host panel status: running but not paired
  ///
  /// In en, this message translates to:
  /// **'Running, not paired'**
  String get hostStatusRunningNotPaired;

  /// IDE Host panel status: paired to a server other than the app's
  ///
  /// In en, this message translates to:
  /// **'Running, paired to a different server'**
  String get hostStatusRunningOtherServer;

  /// IDE Host panel status: running and connected
  ///
  /// In en, this message translates to:
  /// **'Running, paired'**
  String get hostStatusRunningPaired;

  /// IDE Host panel status: paired but reconnecting to the backend
  ///
  /// In en, this message translates to:
  /// **'Running, reconnecting...'**
  String get hostStatusReconnecting;

  /// IDE Host panel status: paired, daemon too old to report connection state
  ///
  /// In en, this message translates to:
  /// **'Running, paired (update aura_hostd)'**
  String get hostStatusPairedUpdateDaemon;

  /// IDE Host panel: backend URL the daemon is paired to
  ///
  /// In en, this message translates to:
  /// **'Backend: {url}'**
  String hostBackendLabel(String url);

  /// IDE Host panel: daemon paired to another server
  ///
  /// In en, this message translates to:
  /// **'This app is signed in to {url}. Pair again to move the daemon there.'**
  String hostOtherServerNotice(String url);

  /// IDE Host panel: start button label while starting
  ///
  /// In en, this message translates to:
  /// **'Starting...'**
  String get hostStarting;

  /// IDE Host panel: start button while an automatic restart is pending
  ///
  /// In en, this message translates to:
  /// **'Start now'**
  String get hostStartNow;

  /// IDE Host panel: start daemon button
  ///
  /// In en, this message translates to:
  /// **'Start'**
  String get hostStart;

  /// IDE Host panel: cancel the pending automatic restart
  ///
  /// In en, this message translates to:
  /// **'Cancel restart'**
  String get hostCancelRestart;

  /// IDE Host panel: stop daemon button
  ///
  /// In en, this message translates to:
  /// **'Stop'**
  String get hostStop;

  /// IDE Host panel: pair button while pairing
  ///
  /// In en, this message translates to:
  /// **'Pairing...'**
  String get hostPairing;

  /// IDE Host panel: pair again to move the daemon to this app's server
  ///
  /// In en, this message translates to:
  /// **'Re-pair'**
  String get hostRepair;

  /// IDE Host panel: pair the daemon with the account
  ///
  /// In en, this message translates to:
  /// **'Pair'**
  String get hostPair;

  /// IDE Host panel: heading above the folders the daemon may access
  ///
  /// In en, this message translates to:
  /// **'Granted Roots'**
  String get hostGrantedRoots;

  /// IDE Host panel: badge on a trusted folder
  ///
  /// In en, this message translates to:
  /// **'Trusted'**
  String get hostTrusted;

  /// IDE Host panel snackbar: no executable path configured
  ///
  /// In en, this message translates to:
  /// **'Set the aura_hostd executable path in Full Settings first.'**
  String get hostSetExecutableFirst;

  /// IDE Host panel snackbar: start failed
  ///
  /// In en, this message translates to:
  /// **'Could not start aura_hostd: {error}'**
  String hostCouldNotStart(String error);

  /// IDE Host panel snackbar: stop failed
  ///
  /// In en, this message translates to:
  /// **'Could not stop aura_hostd: {error}'**
  String hostCouldNotStop(String error);

  /// IDE Host panel: pairing dialog title
  ///
  /// In en, this message translates to:
  /// **'Pair this device'**
  String get hostPairDialogTitle;

  /// IDE Host panel pairing dialog: self-approval in progress
  ///
  /// In en, this message translates to:
  /// **'Approving with your signed-in AURA session...'**
  String get hostPairApproving;

  /// IDE Host panel pairing dialog: waiting for the pairing to complete
  ///
  /// In en, this message translates to:
  /// **'Waiting for approval...'**
  String get hostPairWaiting;

  /// IDE Host panel: pairing error shown inside the 'Could not start pairing' snackbar
  ///
  /// In en, this message translates to:
  /// **'Sign in to AURA first -- pairing needs a valid session to approve with.'**
  String get hostPairSignInFirst;

  /// IDE Host panel snackbar: pairing succeeded
  ///
  /// In en, this message translates to:
  /// **'Paired.'**
  String get hostPaired;

  /// IDE Host panel snackbar: pairing failed; reason is the daemon's raw text
  ///
  /// In en, this message translates to:
  /// **'Pairing failed: {reason}'**
  String hostPairingFailed(String reason);

  /// IDE Host panel snackbar: pairing code expired
  ///
  /// In en, this message translates to:
  /// **'Pairing code expired.'**
  String get hostPairingExpired;

  /// IDE Host panel snackbar: pairing could not start
  ///
  /// In en, this message translates to:
  /// **'Could not start pairing: {error}'**
  String hostCouldNotStartPairing(String error);

  /// IDE Debug Console: no workspace open
  ///
  /// In en, this message translates to:
  /// **'Open a workspace to use the Debug Console.'**
  String get debugOpenWorkspace;

  /// IDE Debug Console: hint of the script path field
  ///
  /// In en, this message translates to:
  /// **'script.py (relative to workspace root)'**
  String get debugScriptHint;

  /// IDE Debug Console toolbar tooltip: start debugging
  ///
  /// In en, this message translates to:
  /// **'Start'**
  String get debugStart;

  /// IDE Debug Console toolbar tooltip: continue execution
  ///
  /// In en, this message translates to:
  /// **'Continue'**
  String get debugContinue;

  /// IDE Debug Console toolbar tooltip: step over
  ///
  /// In en, this message translates to:
  /// **'Step Over'**
  String get debugStepOver;

  /// IDE Debug Console toolbar tooltip: step into
  ///
  /// In en, this message translates to:
  /// **'Step Into'**
  String get debugStepInto;

  /// IDE Debug Console toolbar tooltip: step out
  ///
  /// In en, this message translates to:
  /// **'Step Out'**
  String get debugStepOut;

  /// IDE Debug Console toolbar tooltip: stop debugging
  ///
  /// In en, this message translates to:
  /// **'Stop'**
  String get debugStop;

  /// IDE Debug Console status: no session
  ///
  /// In en, this message translates to:
  /// **'Idle'**
  String get debugStatusIdle;

  /// IDE Debug Console status: starting
  ///
  /// In en, this message translates to:
  /// **'Starting...'**
  String get debugStatusStarting;

  /// IDE Debug Console status: running, with process id
  ///
  /// In en, this message translates to:
  /// **'Running (pid {pid})'**
  String debugStatusRunningPid(int pid);

  /// IDE Debug Console status: running
  ///
  /// In en, this message translates to:
  /// **'Running'**
  String get debugStatusRunning;

  /// IDE Debug Console status: paused at a breakpoint
  ///
  /// In en, this message translates to:
  /// **'Paused'**
  String get debugStatusPaused;

  /// IDE Debug Console status: session ended
  ///
  /// In en, this message translates to:
  /// **'Stopped'**
  String get debugStatusStopped;

  /// IDE Debug Console: label before the breakpoint list
  ///
  /// In en, this message translates to:
  /// **'Breakpoints:'**
  String get debugBreakpointsLabel;

  /// IDE Debug Console: hint of the breakpoint line number field
  ///
  /// In en, this message translates to:
  /// **'line #'**
  String get debugLineHint;

  /// IDE Debug Console: side panel section heading (upper case)
  ///
  /// In en, this message translates to:
  /// **'CALL STACK'**
  String get debugCallStack;

  /// IDE Debug Console: side panel section heading (upper case)
  ///
  /// In en, this message translates to:
  /// **'VARIABLES'**
  String get debugVariables;

  /// Agent Review panel: discard confirmation title
  ///
  /// In en, this message translates to:
  /// **'Discard this task?'**
  String get controlCenterDiscardTitle;

  /// Agent Review panel: discard confirmation body
  ///
  /// In en, this message translates to:
  /// **'This permanently deletes the \"{branch}\" branch and its worktree at {path}. Any unmerged work in it is lost. This cannot be undone.'**
  String controlCenterDiscardBody(String branch, String path);

  /// Agent Review panel: discard a task (button and tooltip)
  ///
  /// In en, this message translates to:
  /// **'Discard'**
  String get controlCenterDiscard;

  /// Agent Review panel: dialog title after a clean merge
  ///
  /// In en, this message translates to:
  /// **'Merged'**
  String get controlCenterMergedTitle;

  /// Agent Review panel: offer to discard the merged worktree
  ///
  /// In en, this message translates to:
  /// **'\"{branch}\" was merged locally. Discard its now-merged worktree?'**
  String controlCenterMergedBody(String branch);

  /// Agent Review panel: keep the merged worktree
  ///
  /// In en, this message translates to:
  /// **'Keep it'**
  String get controlCenterKeepIt;

  /// Agent Review panel: header title
  ///
  /// In en, this message translates to:
  /// **'Agent Review'**
  String get controlCenterTitle;

  /// Agent Review panel: empty state
  ///
  /// In en, this message translates to:
  /// **'No isolated agent tasks right now. Starting a new chat task creates one automatically.'**
  String get controlCenterEmpty;

  /// Agent Review panel: diff stat of a task with no changes
  ///
  /// In en, this message translates to:
  /// **'(no changes)'**
  String get controlCenterNoChanges;

  /// Agent Review panel: diff stat line when it could not be computed; message comes from the daemon
  ///
  /// In en, this message translates to:
  /// **'error: {message}'**
  String controlCenterDiffStatError(String message);

  /// Agent Review panel: error when the base branch is unknown
  ///
  /// In en, this message translates to:
  /// **'Could not determine the base branch to diff against.'**
  String get controlCenterBaseBranchUnknown;

  /// Agent Review panel: merge conflict banner
  ///
  /// In en, this message translates to:
  /// **'Merge conflict -- resolve in the main worktree, then commit there directly.'**
  String get controlCenterMergeConflict;

  /// Agent Review panel: collapse the inline diff
  ///
  /// In en, this message translates to:
  /// **'Hide diff'**
  String get controlCenterHideDiff;

  /// Agent Review panel: expand the inline diff
  ///
  /// In en, this message translates to:
  /// **'View diff'**
  String get controlCenterViewDiff;

  /// Agent Review panel: squash-merge button
  ///
  /// In en, this message translates to:
  /// **'Squash merge'**
  String get controlCenterSquashMerge;

  /// Agent Review panel: merge button while busy
  ///
  /// In en, this message translates to:
  /// **'Working...'**
  String get controlCenterWorking;

  /// Agent Review panel: merge button
  ///
  /// In en, this message translates to:
  /// **'Merge'**
  String get controlCenterMerge;

  /// IDE terminal: no workspace open
  ///
  /// In en, this message translates to:
  /// **'Open a workspace to use the terminal.'**
  String get terminalOpenWorkspace;

  /// IDE terminal: workspace not granted to the daemon
  ///
  /// In en, this message translates to:
  /// **'Grant this workspace to the host daemon to use the terminal.'**
  String get terminalGrantWorkspace;

  /// IDE terminal: workspace not trusted
  ///
  /// In en, this message translates to:
  /// **'Workspace is not trusted. Trust it from the Host panel to use the terminal.'**
  String get terminalUntrusted;

  /// IDE terminal: session starting
  ///
  /// In en, this message translates to:
  /// **'Starting terminal...'**
  String get terminalStarting;

  /// IDE terminal: session ended with an exit code
  ///
  /// In en, this message translates to:
  /// **'Session ended (exit code {code}).'**
  String terminalSessionEndedWithCode(int code);

  /// IDE terminal: session ended without an exit code
  ///
  /// In en, this message translates to:
  /// **'Session ended.'**
  String get terminalSessionEnded;

  /// IDE terminal: restart the session
  ///
  /// In en, this message translates to:
  /// **'Restart'**
  String get terminalRestart;

  /// IDE Output panel: channel filter option showing every channel
  ///
  /// In en, this message translates to:
  /// **'All'**
  String get outputFilterAll;

  /// IDE Output panel: channel filter option
  ///
  /// In en, this message translates to:
  /// **'Agent'**
  String get outputChannelAgent;

  /// IDE Output panel: channel filter option
  ///
  /// In en, this message translates to:
  /// **'Host'**
  String get outputChannelHost;

  /// IDE Output panel: channel filter option
  ///
  /// In en, this message translates to:
  /// **'Tasks'**
  String get outputChannelTasks;

  /// IDE Output panel: channel filter option
  ///
  /// In en, this message translates to:
  /// **'Diagnostics'**
  String get outputChannelDiagnostics;

  /// IDE Output panel: tooltip of the clear button
  ///
  /// In en, this message translates to:
  /// **'Clear Output'**
  String get outputClear;

  /// IDE Output panel: empty state
  ///
  /// In en, this message translates to:
  /// **'No output yet'**
  String get outputEmpty;

  /// IDE Problems panel: no workspace open
  ///
  /// In en, this message translates to:
  /// **'Open a workspace to see problems.'**
  String get diagnosticsOpenWorkspace;

  /// IDE Problems panel: detecting the linter
  ///
  /// In en, this message translates to:
  /// **'Detecting diagnostics tool...'**
  String get diagnosticsDetecting;

  /// IDE Problems panel: no linter found
  ///
  /// In en, this message translates to:
  /// **'No diagnostics tool detected for this workspace.'**
  String get diagnosticsNoTool;

  /// IDE Problems panel: tooltip of the rescan button
  ///
  /// In en, this message translates to:
  /// **'Rerun'**
  String get diagnosticsRerun;

  /// IDE Problems panel: empty state
  ///
  /// In en, this message translates to:
  /// **'No problems detected'**
  String get diagnosticsNoProblems;

  /// IDE Problems panel: linter missing; tool is a command name
  ///
  /// In en, this message translates to:
  /// **'\"{tool}\" is not installed or not on PATH.'**
  String diagnosticsToolNotFound(String tool);

  /// IDE editor: empty state title
  ///
  /// In en, this message translates to:
  /// **'No file open'**
  String get editorNoFileOpen;

  /// IDE editor: empty state hint
  ///
  /// In en, this message translates to:
  /// **'Open a file from the Explorer or press Ctrl+P'**
  String get editorNoFileOpenHint;

  /// IDE editor: file could not be read
  ///
  /// In en, this message translates to:
  /// **'Could not read {path}: {error}'**
  String editorCouldNotRead(String path, String error);

  /// IDE editor: no workspace when opening a file
  ///
  /// In en, this message translates to:
  /// **'No active workspace'**
  String get editorNoActiveWorkspace;

  /// IDE editor: banner after hot-exit restore
  ///
  /// In en, this message translates to:
  /// **'Restored unsaved changes from your last session.'**
  String get editorRestoredNotice;

  /// IDE editor: banner after hot-exit restore when the file changed on disk
  ///
  /// In en, this message translates to:
  /// **'Restored unsaved changes from your last session. The file has changed on disk since; saving will overwrite that change.'**
  String get editorRestoredOverChange;

  /// IDE editor: close-tab dialog title
  ///
  /// In en, this message translates to:
  /// **'Unsaved changes'**
  String get editorUnsavedTitle;

  /// IDE editor: close-tab dialog body; title is the file name
  ///
  /// In en, this message translates to:
  /// **'\"{title}\" has unsaved changes.'**
  String editorUnsavedBody(String title);

  /// IDE editor: close the tab without saving
  ///
  /// In en, this message translates to:
  /// **'Discard'**
  String get editorDiscard;

  /// IDE quick open (Ctrl+P): search field hint
  ///
  /// In en, this message translates to:
  /// **'Type to search files...'**
  String get editorQuickOpenHint;

  /// IDE quick open: no files in the workspace
  ///
  /// In en, this message translates to:
  /// **'No files found'**
  String get editorNoFilesFound;

  /// IDE quick open: no file matches the query
  ///
  /// In en, this message translates to:
  /// **'No matches'**
  String get editorNoMatches;

  /// IDE Agent panel: active workspace path
  ///
  /// In en, this message translates to:
  /// **'Workspace: {path}'**
  String agentWorkspaceLabel(String path);

  /// IDE Agent panel: Engineer persona not allowed
  ///
  /// In en, this message translates to:
  /// **'Engineer is not available on this account'**
  String get agentLockedTitle;

  /// IDE Agent panel: explanation when Engineer is locked
  ///
  /// In en, this message translates to:
  /// **'Engineer needs the engineer_agent capability. Ask an operator to grant it; until then, messages from here would be answered by the general assistant instead.'**
  String get agentLockedMessage;

  /// IDE Agent panel: empty conversation title
  ///
  /// In en, this message translates to:
  /// **'No messages yet'**
  String get agentEmptyTitle;

  /// IDE Agent panel: empty conversation message
  ///
  /// In en, this message translates to:
  /// **'Ask Engineer about this workspace — it can read your files, run commands and propose changes for you to review before anything is applied.'**
  String get agentEmptyMessage;

  /// IDE Agent panel: name of the default task session
  ///
  /// In en, this message translates to:
  /// **'Default'**
  String get agentTaskDefault;

  /// IDE Agent panel: task session name; id is a short session suffix
  ///
  /// In en, this message translates to:
  /// **'Task {id}'**
  String agentTaskLabel(String id);

  /// IDE Agent panel: tooltip of the task switcher
  ///
  /// In en, this message translates to:
  /// **'Switch task'**
  String get agentSwitchTask;

  /// IDE Agent panel: task switcher menu item starting a new task
  ///
  /// In en, this message translates to:
  /// **'+ New Task'**
  String get agentNewTask;

  /// Engineer daemon page: snackbar after saving the aura_hostd executable path
  ///
  /// In en, this message translates to:
  /// **'Saved.'**
  String get engineeringDaemonSaved;

  /// Engineer daemon page: error when Start is pressed with no executable path
  ///
  /// In en, this message translates to:
  /// **'Set the aura_hostd executable path first.'**
  String get engineeringDaemonPathMissing;

  /// Engineer daemon page: snackbar when the daemon could not be started
  ///
  /// In en, this message translates to:
  /// **'Could not start aura_hostd: {error}'**
  String engineeringDaemonStartFailed(String error);

  /// Engineer daemon page: snackbar when the daemon could not be stopped
  ///
  /// In en, this message translates to:
  /// **'Could not stop aura_hostd: {error}'**
  String engineeringDaemonStopFailed(String error);

  /// Engineer daemon page: pairing dialog title and the pair button label
  ///
  /// In en, this message translates to:
  /// **'Pair this device'**
  String get engineeringPairThisDevice;

  /// Engineer daemon page: pairing dialog body while approving
  ///
  /// In en, this message translates to:
  /// **'Approving with your signed-in AURA session...'**
  String get engineeringPairApproving;

  /// Engineer daemon page: pairing dialog line under the spinner
  ///
  /// In en, this message translates to:
  /// **'Waiting for approval...'**
  String get engineeringPairWaiting;

  /// Engineer daemon page: pairing error when the app has no valid session
  ///
  /// In en, this message translates to:
  /// **'Sign in to AURA first -- pairing needs a valid session to approve with.'**
  String get engineeringPairSignInFirst;

  /// Engineer daemon page: pairing error when the server rejected the approval; status is the HTTP code, body the raw server response
  ///
  /// In en, this message translates to:
  /// **'Approval rejected: HTTP {status} {body}'**
  String engineeringPairApprovalRejected(int status, String body);

  /// Engineer daemon page: snackbar after a successful pairing (device name unknown)
  ///
  /// In en, this message translates to:
  /// **'Paired.'**
  String get engineeringPaired;

  /// Engineer daemon page: snackbar after a successful pairing, naming the paired machine
  ///
  /// In en, this message translates to:
  /// **'Paired {name}.'**
  String engineeringPairedNamed(String name);

  /// Engineer daemon page: snackbar when pairing failed; reason comes from the daemon
  ///
  /// In en, this message translates to:
  /// **'Pairing failed: {reason}'**
  String engineeringPairingFailed(String reason);

  /// Engineer daemon page: snackbar when the pairing code expired before approval
  ///
  /// In en, this message translates to:
  /// **'Pairing code expired.'**
  String get engineeringPairingExpired;

  /// Engineer daemon page: snackbar when pairing could not start
  ///
  /// In en, this message translates to:
  /// **'Could not start pairing: {error}'**
  String engineeringPairingStartFailed(String error);

  /// Engineer daemon page: snackbar when a workspace folder could not be granted
  ///
  /// In en, this message translates to:
  /// **'Could not grant {path}: {error}'**
  String engineeringGrantRootFailed(String path, String error);

  /// Engineer daemon page: confirm dialog title for revoking a granted folder
  ///
  /// In en, this message translates to:
  /// **'Revoke workspace root?'**
  String get engineeringRevokeRootTitle;

  /// Engineer daemon page: confirm dialog body for revoking a granted folder
  ///
  /// In en, this message translates to:
  /// **'The agent will no longer be able to read or write anything under {path}.'**
  String engineeringRevokeRootBody(String path);

  /// Engineer daemon page: confirm button and tooltip to revoke a granted folder
  ///
  /// In en, this message translates to:
  /// **'Revoke'**
  String get engineeringRevoke;

  /// Engineer daemon page: app bar title
  ///
  /// In en, this message translates to:
  /// **'Desktop Engineer Daemon'**
  String get engineeringDaemonTitle;

  /// Engineer daemon page: shown when the daemon status check failed
  ///
  /// In en, this message translates to:
  /// **'Could not check status: {error}'**
  String engineeringDaemonStatusFailed(String error);

  /// Engineer daemon page: card title for the daemon executable path
  ///
  /// In en, this message translates to:
  /// **'aura_hostd executable'**
  String get engineeringExecutableTitle;

  /// Engineer daemon page: help text under the executable path title
  ///
  /// In en, this message translates to:
  /// **'Installed builds use the aura_hostd that ships with this app. To use another one, point this at it (see aura_hostd/README.md); clear it to go back to the bundled one.'**
  String get engineeringExecutableHelp;

  /// Engineer daemon page: status when the daemon is not running
  ///
  /// In en, this message translates to:
  /// **'Not running'**
  String get engineeringDaemonNotRunning;

  /// Engineer daemon page: status when the daemon runs but is not paired
  ///
  /// In en, this message translates to:
  /// **'Running, not paired'**
  String get engineeringDaemonRunningNotPaired;

  /// Engineer daemon page: status when the daemon is paired to another server than the app
  ///
  /// In en, this message translates to:
  /// **'Running, paired to a different server'**
  String get engineeringDaemonRunningOtherServer;

  /// Engineer daemon page: status when the daemon is paired and connected
  ///
  /// In en, this message translates to:
  /// **'Running, paired'**
  String get engineeringDaemonRunningPaired;

  /// Engineer daemon page: status while the daemon reconnects to the backend
  ///
  /// In en, this message translates to:
  /// **'Running, reconnecting...'**
  String get engineeringDaemonReconnecting;

  /// Engineer daemon page: status when the daemon is too old to report its connection
  ///
  /// In en, this message translates to:
  /// **'Running, paired -- update aura_hostd to see connection status'**
  String get engineeringDaemonPairedUpdateNeeded;

  /// Engineer daemon page: the backend URL the daemon is paired to
  ///
  /// In en, this message translates to:
  /// **'Backend: {url}'**
  String engineeringDaemonBackend(String url);

  /// Engineer daemon page: warning when the daemon is paired to a different server
  ///
  /// In en, this message translates to:
  /// **'This app is signed in to {url}. Pair again to move the daemon there.'**
  String engineeringDaemonOtherServerNote(String url);

  /// Engineer daemon page: button to start the daemon
  ///
  /// In en, this message translates to:
  /// **'Start'**
  String get engineeringDaemonStart;

  /// Engineer daemon page: button to stop the daemon
  ///
  /// In en, this message translates to:
  /// **'Stop'**
  String get engineeringDaemonStop;

  /// Engineer daemon page: button to pair again with the current server
  ///
  /// In en, this message translates to:
  /// **'Pair again'**
  String get engineeringPairAgain;

  /// Engineer daemon page: card title listing granted folders
  ///
  /// In en, this message translates to:
  /// **'Workspace roots'**
  String get engineeringWorkspaceRoots;

  /// Engineer daemon page: empty state for granted folders
  ///
  /// In en, this message translates to:
  /// **'No folders granted yet -- the agent can\'t read or write anything on this machine until you add one.'**
  String get engineeringNoRoots;

  /// Engineer daemon page: subtitle of a trusted granted folder
  ///
  /// In en, this message translates to:
  /// **'Trusted -- commands run without per-call confirmation (still confined to this folder)'**
  String get engineeringRootTrusted;

  /// Engineer daemon page: subtitle of an untrusted granted folder
  ///
  /// In en, this message translates to:
  /// **'Untrusted -- only a read-only inspection allowlist runs'**
  String get engineeringRootUntrusted;

  /// Engineer daemon page: tooltip to browse a granted folder
  ///
  /// In en, this message translates to:
  /// **'Browse'**
  String get engineeringBrowse;

  /// Engineer daemon page: card title for the live daemon log
  ///
  /// In en, this message translates to:
  /// **'Daemon output'**
  String get engineeringDaemonOutput;

  /// Engineer daemon page: note shown when the daemon was not started by this app
  ///
  /// In en, this message translates to:
  /// **'Live output is only available for a daemon this app started -- pointing at one already running elsewhere still works for everything else on this page.'**
  String get engineeringNoLogsNote;

  /// Host file browser: button to leave the file view
  ///
  /// In en, this message translates to:
  /// **'Back to file list'**
  String get engineeringBackToFileList;

  /// Host file browser: note when a file preview was truncated
  ///
  /// In en, this message translates to:
  /// **'File is larger than this preview shows.'**
  String get engineeringFileTruncated;

  /// Host file browser: error when a folder could not be listed
  ///
  /// In en, this message translates to:
  /// **'Could not read this directory: {error}'**
  String engineeringReadDirFailed(String error);

  /// Host file browser: empty folder state
  ///
  /// In en, this message translates to:
  /// **'This folder is empty.'**
  String get engineeringFolderEmpty;

  /// GitHub connection page: snackbar when the browser could not open
  ///
  /// In en, this message translates to:
  /// **'Could not open the browser to connect GitHub.'**
  String get engineeringGithubBrowserFailed;

  /// GitHub connection page: snackbar after disconnecting
  ///
  /// In en, this message translates to:
  /// **'GitHub disconnected.'**
  String get engineeringGithubDisconnected;

  /// GitHub connection page: status title when connected but the login is unknown
  ///
  /// In en, this message translates to:
  /// **'Connected as a GitHub account'**
  String get engineeringGithubConnectedUnknown;

  /// GitHub connection page: status title naming the connected GitHub login
  ///
  /// In en, this message translates to:
  /// **'Connected as {login}'**
  String engineeringGithubConnectedAs(String login);

  /// GitHub connection page: status title when nothing is connected
  ///
  /// In en, this message translates to:
  /// **'No GitHub account connected'**
  String get engineeringGithubNotConnected;

  /// GitHub connection page: when the account was connected; date is already formatted
  ///
  /// In en, this message translates to:
  /// **'Connected {date}'**
  String engineeringGithubConnectedAt(String date);

  /// GitHub connection page: explanation while connected
  ///
  /// In en, this message translates to:
  /// **'The engineer agent can read and write exactly the repositories you granted when you installed the GitHub App. Disconnecting only removes what AURA can use -- it does not revoke the app on github.com.'**
  String get engineeringGithubConnectedBody;

  /// GitHub connection page: explanation while not connected
  ///
  /// In en, this message translates to:
  /// **'Connect your own GitHub account so the engineer agent (opening PRs, reading issues, committing files) acts on the repositories you choose, instead of relying on a shared token an operator configured.'**
  String get engineeringGithubDisconnectedBody;

  /// GitHub connection page: button to disconnect
  ///
  /// In en, this message translates to:
  /// **'Disconnect GitHub'**
  String get engineeringGithubDisconnect;

  /// GitHub connection page: button to connect
  ///
  /// In en, this message translates to:
  /// **'Connect GitHub'**
  String get engineeringGithubConnect;

  /// Engineer history page: app bar action tooltip opening the desktop daemon page
  ///
  /// In en, this message translates to:
  /// **'Desktop daemon'**
  String get engineeringDesktopDaemonTooltip;

  /// Engineer history page: empty state title
  ///
  /// In en, this message translates to:
  /// **'No engineer runs yet'**
  String get engineeringNoRunsTitle;

  /// Engineer history page: empty state message
  ///
  /// In en, this message translates to:
  /// **'Ask the Engineer agent to open a PR or run a command, and it will show up here.'**
  String get engineeringNoRunsMessage;

  /// Engineer history: run label for opening a draft pull request
  ///
  /// In en, this message translates to:
  /// **'Opened draft PR'**
  String get engineeringToolOpenedDraftPr;

  /// Engineer history: run label for committing files on GitHub
  ///
  /// In en, this message translates to:
  /// **'Committed files'**
  String get engineeringToolCommittedFiles;

  /// Engineer history: run label for creating a branch
  ///
  /// In en, this message translates to:
  /// **'Created branch'**
  String get engineeringToolCreatedBranch;

  /// Engineer history: run label for writing a file
  ///
  /// In en, this message translates to:
  /// **'Wrote file'**
  String get engineeringToolWroteFile;

  /// Engineer history: run label for editing a file
  ///
  /// In en, this message translates to:
  /// **'Edited file'**
  String get engineeringToolEditedFile;

  /// Engineer history: run label for running a command
  ///
  /// In en, this message translates to:
  /// **'Ran command'**
  String get engineeringToolRanCommand;

  /// Engineer history: run label for a git commit
  ///
  /// In en, this message translates to:
  /// **'Committed'**
  String get engineeringToolCommitted;

  /// Engineer history: run label for checking out a branch
  ///
  /// In en, this message translates to:
  /// **'Switched branch'**
  String get engineeringToolSwitchedBranch;

  /// Engineer history: run label for diffing the sandbox
  ///
  /// In en, this message translates to:
  /// **'Diffed sandbox'**
  String get engineeringToolDiffedSandbox;

  /// Engineer history: run status word (pending)
  ///
  /// In en, this message translates to:
  /// **'pending'**
  String get engineeringRunStatusPending;

  /// Engineer history: run status word (succeeded)
  ///
  /// In en, this message translates to:
  /// **'succeeded'**
  String get engineeringRunStatusSucceeded;

  /// Engineer history: run status word (failed)
  ///
  /// In en, this message translates to:
  /// **'failed'**
  String get engineeringRunStatusFailed;

  /// Engineer history: run detail status line
  ///
  /// In en, this message translates to:
  /// **'Status: {status}'**
  String engineeringRunStatus(String status);

  /// Engineer history: run detail branch line
  ///
  /// In en, this message translates to:
  /// **'Branch: {branch}'**
  String engineeringRunBranch(String branch);

  /// Engineer history: run detail commit line
  ///
  /// In en, this message translates to:
  /// **'Commit: {sha}'**
  String engineeringRunCommit(String sha);

  /// Engineer history: tooltip to copy the PR link
  ///
  /// In en, this message translates to:
  /// **'Copy link'**
  String get engineeringCopyLink;

  /// Engineer history: snackbar after copying the PR link
  ///
  /// In en, this message translates to:
  /// **'Link copied'**
  String get engineeringLinkCopied;

  /// Engineer history: heading over a run transcript or diff
  ///
  /// In en, this message translates to:
  /// **'Output'**
  String get engineeringRunOutput;

  /// Automations page: title
  ///
  /// In en, this message translates to:
  /// **'AURA Automations'**
  String get automationsTitle;

  /// Automations page: subtitle
  ///
  /// In en, this message translates to:
  /// **'Extract tasks from emails and issues, generate workspace structures'**
  String get automationsSubtitle;

  /// Automations page: Gmail tab label
  ///
  /// In en, this message translates to:
  /// **'Gmail Inbox Analyzer'**
  String get automationsTabGmail;

  /// Automations page: GitHub tab label
  ///
  /// In en, this message translates to:
  /// **'GitHub Issue Importer'**
  String get automationsTabGithub;

  /// Automations page: Drive tab label
  ///
  /// In en, this message translates to:
  /// **'Drive Workspace Builder'**
  String get automationsTabDrive;

  /// Automations Gmail tab: title shown for a mock developer profile
  ///
  /// In en, this message translates to:
  /// **'Live Gmail Integration Unavailable'**
  String get automationsGmailUnavailableTitle;

  /// Automations Gmail tab: body shown for a mock developer profile
  ///
  /// In en, this message translates to:
  /// **'You are signed in via a mock developer profile. Real unread emails can only be fetched when logged in with a real Google Account.'**
  String get automationsGmailUnavailableBody;

  /// Automations Gmail tab: section title
  ///
  /// In en, this message translates to:
  /// **'Recent Unread Emails'**
  String get automationsGmailRecentUnread;

  /// Automations Gmail tab: button to fetch unread emails
  ///
  /// In en, this message translates to:
  /// **'Fetch Unread'**
  String get automationsGmailFetchUnread;

  /// Automations Gmail tab: loading text
  ///
  /// In en, this message translates to:
  /// **'Fetching...'**
  String get automationsGmailFetching;

  /// Automations Gmail tab: empty state
  ///
  /// In en, this message translates to:
  /// **'No unread emails loaded. Click Fetch.'**
  String get automationsGmailEmpty;

  /// Automations Gmail tab: email sender line
  ///
  /// In en, this message translates to:
  /// **'From: {sender}'**
  String automationsGmailFrom(String sender);

  /// Automations Gmail tab: button to analyze an email with AI
  ///
  /// In en, this message translates to:
  /// **'AI Analyze'**
  String get automationsGmailAnalyze;

  /// Automations Gmail tab: analysed email category chip; category comes from the server
  ///
  /// In en, this message translates to:
  /// **'Category: {category}'**
  String automationsGmailCategory(String category);

  /// Automations Gmail tab: urgency chip for high urgency
  ///
  /// In en, this message translates to:
  /// **'URGENT'**
  String get automationsGmailUrgent;

  /// Automations Gmail tab: urgency chip for low urgency
  ///
  /// In en, this message translates to:
  /// **'Low Urgency'**
  String get automationsGmailLowUrgency;

  /// Automations Gmail tab: heading over the AI summary
  ///
  /// In en, this message translates to:
  /// **'AI Summary'**
  String get automationsGmailAiSummary;

  /// Automations Gmail tab: heading over extracted action items
  ///
  /// In en, this message translates to:
  /// **'Action Items Extracted'**
  String get automationsGmailActionItems;

  /// Automations Gmail tab: suggested due date of an action item; date is already formatted
  ///
  /// In en, this message translates to:
  /// **'Suggested Deadline: {date}'**
  String automationsGmailSuggestedDeadline(String date);

  /// Automations Gmail tab: tooltip to import an action item as a task
  ///
  /// In en, this message translates to:
  /// **'Import as AURA Task'**
  String get automationsGmailImportTask;

  /// Automations Gmail tab: snackbar after importing a task
  ///
  /// In en, this message translates to:
  /// **'Imported task: {title}'**
  String automationsGmailImportedTask(String title);

  /// Automations Gmail tab: heading over a suggested calendar event
  ///
  /// In en, this message translates to:
  /// **'Suggested Calendar Event'**
  String get automationsGmailSuggestedEvent;

  /// Automations Gmail tab: tooltip to add the suggested event to the calendar
  ///
  /// In en, this message translates to:
  /// **'Schedule Event'**
  String get automationsGmailScheduleEvent;

  /// Automations Gmail tab: snackbar after adding the event
  ///
  /// In en, this message translates to:
  /// **'Scheduled event: {title}'**
  String automationsGmailScheduledEvent(String title);

  /// Automations Gmail tab: snackbar when fetching emails failed
  ///
  /// In en, this message translates to:
  /// **'Gmail Fetch Error: {error}'**
  String automationsGmailFetchError(String error);

  /// Automations Gmail tab: snackbar when analysing an email failed
  ///
  /// In en, this message translates to:
  /// **'Analysis failed: {error}'**
  String automationsGmailAnalysisFailed(String error);

  /// Automations Gmail tab: placeholder for an email with no subject
  ///
  /// In en, this message translates to:
  /// **'No Subject'**
  String get automationsGmailNoSubject;

  /// Automations Gmail tab: placeholder for an email with no sender
  ///
  /// In en, this message translates to:
  /// **'Unknown Sender'**
  String get automationsGmailUnknownSender;

  /// Automations GitHub tab: card title
  ///
  /// In en, this message translates to:
  /// **'GitHub Integration Setup'**
  String get automationsGithubSetup;

  /// Automations GitHub tab: label of the personal access token field
  ///
  /// In en, this message translates to:
  /// **'GitHub PAT (Personal Access Token)'**
  String get automationsGithubPatLabel;

  /// Automations GitHub tab: label of the owner/repo field
  ///
  /// In en, this message translates to:
  /// **'Repository Path'**
  String get automationsGithubRepoLabel;

  /// Automations GitHub tab: button to load open issues
  ///
  /// In en, this message translates to:
  /// **'Load Repo Issues'**
  String get automationsGithubLoadIssues;

  /// Automations GitHub tab: loading text
  ///
  /// In en, this message translates to:
  /// **'Loading GitHub Board...'**
  String get automationsGithubLoading;

  /// Automations GitHub tab: empty state
  ///
  /// In en, this message translates to:
  /// **'No open issues loaded.'**
  String get automationsGithubEmpty;

  /// Automations GitHub tab: button to analyse an issue into subtasks
  ///
  /// In en, this message translates to:
  /// **'Extract Subtasks'**
  String get automationsGithubExtractSubtasks;

  /// Automations GitHub tab: complexity chip; value comes from the server
  ///
  /// In en, this message translates to:
  /// **'Complexity: {complexity}'**
  String automationsGithubComplexity(String complexity);

  /// Automations GitHub tab: priority chip; value comes from the server
  ///
  /// In en, this message translates to:
  /// **'Priority: {priority}'**
  String automationsGithubPriority(String priority);

  /// Automations GitHub tab: heading over the issue summary
  ///
  /// In en, this message translates to:
  /// **'Issue Summary'**
  String get automationsGithubIssueSummary;

  /// Automations GitHub tab: heading over extracted coding tasks
  ///
  /// In en, this message translates to:
  /// **'Extracted Coding Tasks'**
  String get automationsGithubCodingTasks;

  /// Automations GitHub tab: snackbar after importing a coding task
  ///
  /// In en, this message translates to:
  /// **'Imported coding task!'**
  String get automationsGithubImported;

  /// Automations GitHub tab: validation error when the token is missing
  ///
  /// In en, this message translates to:
  /// **'Please provide both GitHub PAT and Repository Path.'**
  String get automationsGithubMissingInput;

  /// Automations GitHub tab: validation error for a bad repository path. The translation must keep the literal text owner/repo (the snackbar colour depends on it)
  ///
  /// In en, this message translates to:
  /// **'Repository Path must be in \'owner/repo\' format (e.g., owner/AURA-AI).'**
  String get automationsGithubRepoFormat;

  /// Automations GitHub tab: snackbar when loading issues failed
  ///
  /// In en, this message translates to:
  /// **'GitHub fetch failed: {error}'**
  String automationsGithubFetchFailed(String error);

  /// Automations GitHub tab: snackbar when analysing an issue failed
  ///
  /// In en, this message translates to:
  /// **'Issue analysis failed: {error}'**
  String automationsGithubAnalysisFailed(String error);

  /// Automations Drive tab: title shown for a mock developer profile
  ///
  /// In en, this message translates to:
  /// **'Google Drive Workspace Creation Unavailable'**
  String get automationsDriveUnavailableTitle;

  /// Automations Drive tab: body shown for a mock developer profile
  ///
  /// In en, this message translates to:
  /// **'You are signed in via a mock developer profile. Drive workspace directories can only be generated when logged in with a real Google Account.'**
  String get automationsDriveUnavailableBody;

  /// Automations Drive tab: card title
  ///
  /// In en, this message translates to:
  /// **'Drive Project Workspace Generator'**
  String get automationsDriveGeneratorTitle;

  /// Automations Drive tab: card description
  ///
  /// In en, this message translates to:
  /// **'Enter project details. AURA will generate a Google Drive folder and populate it with a structured Markdown project outline document.'**
  String get automationsDriveGeneratorBody;

  /// Automations Drive tab: project name field label
  ///
  /// In en, this message translates to:
  /// **'Project Name'**
  String get automationsDriveProjectName;

  /// Automations Drive tab: project name field hint
  ///
  /// In en, this message translates to:
  /// **'e.g. Freelance Mobile App'**
  String get automationsDriveProjectNameHint;

  /// Automations Drive tab: project description field label
  ///
  /// In en, this message translates to:
  /// **'Project Description / Outline'**
  String get automationsDriveProjectDescription;

  /// Automations Drive tab: project description field hint
  ///
  /// In en, this message translates to:
  /// **'Describe details to generate template plan'**
  String get automationsDriveProjectDescriptionHint;

  /// Automations Drive tab: button to create the Drive workspace
  ///
  /// In en, this message translates to:
  /// **'Generate Drive Workspace'**
  String get automationsDriveGenerate;

  /// Automations Drive tab: title over the progress log
  ///
  /// In en, this message translates to:
  /// **'Automation Activity Console'**
  String get automationsDriveConsole;

  /// Automations Drive tab: first progress log line
  ///
  /// In en, this message translates to:
  /// **'Initializing project workspace folders...'**
  String get automationsDriveInitializing;

  /// Automations Drive tab: progress log line when creation failed
  ///
  /// In en, this message translates to:
  /// **'Workspace creation failed: {error}'**
  String automationsDriveFailed(String error);

  /// Automations Drive tab: progress log line while asking for Drive access
  ///
  /// In en, this message translates to:
  /// **'Requesting Drive storage permissions...'**
  String get automationsDriveRequestingPermissions;

  /// Automations Drive tab: progress log line after connecting to Drive
  ///
  /// In en, this message translates to:
  /// **'Established connection with Google Drive.'**
  String get automationsDriveConnected;

  /// Automations Drive tab: progress log line after the folder is created; name is the folder name as created on Drive
  ///
  /// In en, this message translates to:
  /// **'Created Folder: {name}'**
  String automationsDriveCreatedFolder(String name);

  /// Automations Drive tab: progress log line after the plan document is created; name is the document name as created on Drive
  ///
  /// In en, this message translates to:
  /// **'Created document: {name}'**
  String automationsDriveCreatedDocument(String name);

  /// Automations Drive tab: last progress log line on success
  ///
  /// In en, this message translates to:
  /// **'Drive Workspace generated successfully!'**
  String get automationsDriveDone;

  /// Finance page: title
  ///
  /// In en, this message translates to:
  /// **'Finance Ledger'**
  String get financeTitle;

  /// Finance page: subtitle
  ///
  /// In en, this message translates to:
  /// **'Track incomes, expenses, and analyze financial distributions'**
  String get financeSubtitle;

  /// Finance summary card: balance title
  ///
  /// In en, this message translates to:
  /// **'Balance'**
  String get financeBalance;

  /// Finance: income label (summary card, chart axis, type chip)
  ///
  /// In en, this message translates to:
  /// **'Income'**
  String get financeIncome;

  /// Finance summary card: total expenses title
  ///
  /// In en, this message translates to:
  /// **'Expenses'**
  String get financeExpenses;

  /// Finance charts: pie chart title
  ///
  /// In en, this message translates to:
  /// **'Expenses by Category'**
  String get financeExpensesByCategory;

  /// Finance charts: empty pie chart
  ///
  /// In en, this message translates to:
  /// **'No expenses logged'**
  String get financeNoExpenses;

  /// Finance charts: bar chart title
  ///
  /// In en, this message translates to:
  /// **'Income vs Expense'**
  String get financeIncomeVsExpense;

  /// Finance: expense label (chart axis, type chip)
  ///
  /// In en, this message translates to:
  /// **'Expense'**
  String get financeExpense;

  /// Finance form: card title
  ///
  /// In en, this message translates to:
  /// **'Log Transaction'**
  String get financeLogTransaction;

  /// Finance form: amount field label; currency is the currency symbol
  ///
  /// In en, this message translates to:
  /// **'Amount ({currency})'**
  String financeAmountLabel(String currency);

  /// Finance form: category field label
  ///
  /// In en, this message translates to:
  /// **'Category'**
  String get financeCategory;

  /// Finance category name (display only; stored value stays English)
  ///
  /// In en, this message translates to:
  /// **'Food'**
  String get financeCategoryFood;

  /// Finance category name (display only; stored value stays English)
  ///
  /// In en, this message translates to:
  /// **'Studies'**
  String get financeCategoryStudies;

  /// Finance category name (display only; stored value stays English)
  ///
  /// In en, this message translates to:
  /// **'Freelance'**
  String get financeCategoryFreelance;

  /// Finance category name (display only; stored value stays English)
  ///
  /// In en, this message translates to:
  /// **'Housing'**
  String get financeCategoryHousing;

  /// Finance category name (display only; stored value stays English)
  ///
  /// In en, this message translates to:
  /// **'Entertainment'**
  String get financeCategoryEntertainment;

  /// Finance category name (display only; stored value stays English)
  ///
  /// In en, this message translates to:
  /// **'Utilities'**
  String get financeCategoryUtilities;

  /// Finance category name (display only; stored value stays English)
  ///
  /// In en, this message translates to:
  /// **'Transport'**
  String get financeCategoryTransport;

  /// Finance category name (display only; stored value stays English)
  ///
  /// In en, this message translates to:
  /// **'Other'**
  String get financeCategoryOther;

  /// Finance form: description field label
  ///
  /// In en, this message translates to:
  /// **'Description (Optional)'**
  String get financeDescriptionLabel;

  /// Finance form: description field hint
  ///
  /// In en, this message translates to:
  /// **'e.g. Weekly grocery shopping'**
  String get financeDescriptionHint;

  /// Finance form: date row title
  ///
  /// In en, this message translates to:
  /// **'Date'**
  String get financeDate;

  /// Finance form: snackbar after saving a transaction
  ///
  /// In en, this message translates to:
  /// **'Transaction logged successfully!'**
  String get financeLogged;

  /// Finance form: submit button
  ///
  /// In en, this message translates to:
  /// **'Submit Ledger Entry'**
  String get financeSubmit;

  /// Finance form: validation error for the amount
  ///
  /// In en, this message translates to:
  /// **'Please enter a valid positive amount.'**
  String get financeInvalidAmount;

  /// Finance form: validation error when no category is chosen
  ///
  /// In en, this message translates to:
  /// **'Please choose a category.'**
  String get financeChooseCategory;

  /// Finance history: card title
  ///
  /// In en, this message translates to:
  /// **'Recent Activities'**
  String get financeRecentActivities;

  /// Finance history: empty state
  ///
  /// In en, this message translates to:
  /// **'No activities recorded yet'**
  String get financeNoActivities;

  /// Finance history: snackbar after swiping a transaction away; entry is its description or category
  ///
  /// In en, this message translates to:
  /// **'Deleted entry: {entry}'**
  String financeDeletedEntry(String entry);

  /// Desktop IDE shell: screen-reader label of the scrim behind a floating panel
  ///
  /// In en, this message translates to:
  /// **'Close panel'**
  String get ideClosePanel;

  /// Desktop IDE activity bar: Explorer item label
  ///
  /// In en, this message translates to:
  /// **'Explorer'**
  String get ideActivityExplorer;

  /// Desktop IDE activity bar: Explorer item tooltip with its shortcut
  ///
  /// In en, this message translates to:
  /// **'Explorer (Ctrl+B)'**
  String get ideActivityExplorerTooltip;

  /// Desktop IDE activity bar: Quick Open item label
  ///
  /// In en, this message translates to:
  /// **'Go to File'**
  String get ideActivityGoToFile;

  /// Desktop IDE activity bar: Quick Open item tooltip with its shortcut
  ///
  /// In en, this message translates to:
  /// **'Go to File (Ctrl+P)'**
  String get ideActivityGoToFileTooltip;

  /// Desktop IDE activity bar: Source Control (Git) item label and tooltip
  ///
  /// In en, this message translates to:
  /// **'Source Control'**
  String get ideActivitySourceControl;

  /// Desktop IDE activity bar: AI agent panel item label
  ///
  /// In en, this message translates to:
  /// **'AURA Agents'**
  String get ideActivityAgents;

  /// Desktop IDE activity bar: AI agent panel item tooltip with its shortcut
  ///
  /// In en, this message translates to:
  /// **'AURA Agents (Ctrl+J)'**
  String get ideActivityAgentsTooltip;

  /// Desktop IDE activity bar: Control Center item tooltip
  ///
  /// In en, this message translates to:
  /// **'AURA Control Center'**
  String get ideActivityControlCenterTooltip;

  /// Desktop IDE activity bar: bottom panel toggle label
  ///
  /// In en, this message translates to:
  /// **'Panel'**
  String get ideActivityPanel;

  /// Desktop IDE activity bar: bottom panel toggle tooltip with its shortcut
  ///
  /// In en, this message translates to:
  /// **'Terminal, Output & Problems (Ctrl+Shift+J)'**
  String get ideActivityPanelTooltip;

  /// Desktop IDE activity bar: screen-reader label of the bottom panel toggle when it carries a problem-count badge
  ///
  /// In en, this message translates to:
  /// **'Panel, {count, plural, =1{1 problem} other{{count} problems}}'**
  String ideActivityPanelProblems(int count);

  /// Desktop IDE bottom panel: Terminal tab
  ///
  /// In en, this message translates to:
  /// **'Terminal'**
  String get ideBottomTerminal;

  /// Desktop IDE bottom panel: Output tab
  ///
  /// In en, this message translates to:
  /// **'Output'**
  String get ideBottomOutput;

  /// Desktop IDE bottom panel: Problems tab with the number of problems
  ///
  /// In en, this message translates to:
  /// **'Problems ({count})'**
  String ideBottomProblemsCount(int count);

  /// Desktop IDE bottom panel: Problems tab with no problems
  ///
  /// In en, this message translates to:
  /// **'Problems'**
  String get ideBottomProblems;

  /// Desktop IDE bottom panel: Debug Console tab
  ///
  /// In en, this message translates to:
  /// **'Debug Console'**
  String get ideBottomDebugConsole;

  /// Desktop IDE command palette: why a command is disabled
  ///
  /// In en, this message translates to:
  /// **'No workspace is open'**
  String get ideCmdNoWorkspace;

  /// Desktop IDE command palette: why a command is disabled
  ///
  /// In en, this message translates to:
  /// **'No file is open'**
  String get ideCmdNoFileOpen;

  /// Desktop IDE command palette: group heading for file commands
  ///
  /// In en, this message translates to:
  /// **'File'**
  String get ideCmdGroupFile;

  /// Desktop IDE command palette: group heading for layout commands
  ///
  /// In en, this message translates to:
  /// **'View'**
  String get ideCmdGroupView;

  /// Desktop IDE command palette: Quick Open command
  ///
  /// In en, this message translates to:
  /// **'Go to File...'**
  String get ideCmdGoToFile;

  /// Desktop IDE command palette: save command
  ///
  /// In en, this message translates to:
  /// **'Save File'**
  String get ideCmdSaveFile;

  /// Desktop IDE command palette: why Save File is disabled
  ///
  /// In en, this message translates to:
  /// **'No unsaved changes'**
  String get ideCmdNoUnsavedChanges;

  /// Desktop IDE command palette: close the active tab
  ///
  /// In en, this message translates to:
  /// **'Close Editor'**
  String get ideCmdCloseEditor;

  /// Desktop IDE command palette
  ///
  /// In en, this message translates to:
  /// **'Show Explorer'**
  String get ideCmdShowExplorer;

  /// Desktop IDE command palette
  ///
  /// In en, this message translates to:
  /// **'Show Source Control'**
  String get ideCmdShowSourceControl;

  /// Desktop IDE command palette
  ///
  /// In en, this message translates to:
  /// **'Show AURA Control Center'**
  String get ideCmdShowControlCenter;

  /// Desktop IDE command palette: toggle the primary sidebar (currently shown)
  ///
  /// In en, this message translates to:
  /// **'Hide Side Bar'**
  String get ideCmdHideSidebar;

  /// Desktop IDE command palette: toggle the primary sidebar (currently hidden)
  ///
  /// In en, this message translates to:
  /// **'Show Side Bar'**
  String get ideCmdShowSidebar;

  /// Desktop IDE command palette: toggle the bottom panel (currently shown)
  ///
  /// In en, this message translates to:
  /// **'Hide Panel'**
  String get ideCmdHidePanel;

  /// Desktop IDE command palette: toggle the bottom panel (currently hidden)
  ///
  /// In en, this message translates to:
  /// **'Show Panel'**
  String get ideCmdShowPanel;

  /// Desktop IDE command palette: open the panel and focus the terminal
  ///
  /// In en, this message translates to:
  /// **'Focus Terminal'**
  String get ideCmdFocusTerminal;

  /// Desktop IDE command palette: toggle the agent panel (currently shown)
  ///
  /// In en, this message translates to:
  /// **'Hide AURA Agent'**
  String get ideCmdHideAgent;

  /// Desktop IDE command palette: toggle the agent panel (currently hidden)
  ///
  /// In en, this message translates to:
  /// **'Show AURA Agent'**
  String get ideCmdShowAgent;

  /// Desktop IDE command palette: ask AURA for an inline code completion at the cursor
  ///
  /// In en, this message translates to:
  /// **'Request Inline Completion'**
  String get ideCmdInlineCompletion;

  /// Desktop IDE command palette: refresh Git status
  ///
  /// In en, this message translates to:
  /// **'Refresh Source Control'**
  String get ideCmdRefreshSourceControl;

  /// Desktop IDE command palette: go back to AURA's Today page
  ///
  /// In en, this message translates to:
  /// **'Leave IDE (Open Today)'**
  String get ideCmdLeaveIde;

  /// Desktop IDE status bar: no Git branch (no repo yet, or detached HEAD)
  ///
  /// In en, this message translates to:
  /// **'No branch'**
  String get ideStatusNoBranch;

  /// Desktop IDE status bar: host daemon unreachable
  ///
  /// In en, this message translates to:
  /// **'Host Not Running'**
  String get ideStatusHostNotRunning;

  /// Desktop IDE status bar: host daemon running but not paired
  ///
  /// In en, this message translates to:
  /// **'Host Not Paired'**
  String get ideStatusHostNotPaired;

  /// Desktop IDE status bar: host daemon paired to another backend
  ///
  /// In en, this message translates to:
  /// **'Host paired to a different server -- re-pair'**
  String get ideStatusHostOtherServer;

  /// Desktop IDE status bar: host daemon connected
  ///
  /// In en, this message translates to:
  /// **'Host Connected'**
  String get ideStatusHostConnected;

  /// Desktop IDE status bar: host daemon reconnecting to the backend
  ///
  /// In en, this message translates to:
  /// **'Reconnecting to server...'**
  String get ideStatusReconnecting;

  /// Desktop IDE status bar: daemon too old to report its connection
  ///
  /// In en, this message translates to:
  /// **'Host Status Unknown (update aura_hostd)'**
  String get ideStatusHostUnknown;

  /// Desktop IDE status bar: number of agent actions waiting for approval
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 pending approval} other{{count} pending approvals}}'**
  String ideStatusPendingApprovals(int count);

  /// Desktop IDE status bar: tooltip when the update channel was rolled back
  ///
  /// In en, this message translates to:
  /// **'This version was rolled back to {version}'**
  String ideStatusRolledBackTooltip(String version);

  /// Desktop IDE status bar: tooltip when an update is available
  ///
  /// In en, this message translates to:
  /// **'AURA {version} is available'**
  String ideStatusUpdateAvailableTooltip(String version);

  /// Desktop IDE status bar: rollback offer
  ///
  /// In en, this message translates to:
  /// **'Roll back to {version}'**
  String ideStatusRollBackTo(String version);

  /// Desktop IDE status bar: update offer
  ///
  /// In en, this message translates to:
  /// **'Update to {version}'**
  String ideStatusUpdateTo(String version);

  /// Desktop IDE status bar: cursor line and column
  ///
  /// In en, this message translates to:
  /// **'Ln {line}, Col {column}'**
  String ideStatusLineColumn(int line, int column);

  /// Desktop IDE status bar: the file mixes LF/CRLF/CR line endings
  ///
  /// In en, this message translates to:
  /// **'Mixed'**
  String get ideStatusLineEndingMixed;

  /// Desktop update dialog title (rollback)
  ///
  /// In en, this message translates to:
  /// **'Roll back to AURA {version}?'**
  String ideUpdateRollbackTitle(String version);

  /// Desktop update dialog title
  ///
  /// In en, this message translates to:
  /// **'Update to AURA {version}?'**
  String ideUpdateTitle(String version);

  /// Desktop update dialog body on Windows (rollback)
  ///
  /// In en, this message translates to:
  /// **'This version has been rolled back. AURA will close, install {version} and reopen. Unsaved changes are kept and restored.'**
  String ideUpdateWindowsRollbackBody(String version);

  /// Desktop update dialog body on Windows
  ///
  /// In en, this message translates to:
  /// **'AURA will close, install {version} and reopen. Unsaved changes are kept and restored.'**
  String ideUpdateWindowsBody(String version);

  /// Desktop update dialog body on Linux
  ///
  /// In en, this message translates to:
  /// **'Download AURA {version} for Linux in your browser.'**
  String ideUpdateLinuxBody(String version);

  /// Desktop IDE: decline button in the update and grant-access dialogs
  ///
  /// In en, this message translates to:
  /// **'Not now'**
  String get ideNotNow;

  /// Desktop update dialog: confirm button on Windows
  ///
  /// In en, this message translates to:
  /// **'Install and restart'**
  String get ideUpdateInstallRestart;

  /// Desktop update dialog: confirm button on Linux
  ///
  /// In en, this message translates to:
  /// **'Download'**
  String get ideUpdateDownload;

  /// Desktop update: progress dialog
  ///
  /// In en, this message translates to:
  /// **'Downloading the update...'**
  String get ideUpdateDownloading;

  /// Desktop update: error snackbar when the installer fails its checksum
  ///
  /// In en, this message translates to:
  /// **'The download did not match the release checksum and was deleted. Nothing was installed.'**
  String get ideUpdateChecksumFailed;

  /// Desktop update: error snackbar
  ///
  /// In en, this message translates to:
  /// **'Could not update: {error}'**
  String ideUpdateFailed(String error);

  /// Desktop IDE: title of the dialog listing agent actions waiting for approval
  ///
  /// In en, this message translates to:
  /// **'Pending Approvals'**
  String get idePendingApprovalsTitle;

  /// Desktop IDE approvals dialog: how many files a pending write touches, and in which workspace root
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 file in {root}} other{{count} files in {root}}}'**
  String ideApprovalFilesInRoot(int count, String root);

  /// Desktop IDE approvals dialog: how many files a pending write touches
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 file} other{{count} files}}'**
  String ideApprovalFiles(int count);

  /// Desktop IDE approvals dialog: approve button
  ///
  /// In en, this message translates to:
  /// **'Approve'**
  String get ideApprove;

  /// Explorer / workspace pages: dialog title asking to grant the folder to the host daemon
  ///
  /// In en, this message translates to:
  /// **'Grant workspace to hostd?'**
  String get explorerGrantTitle;

  /// Explorer: grant-access dialog body
  ///
  /// In en, this message translates to:
  /// **'This allows the AURA agent to read and write files in \"{root}\".'**
  String explorerGrantBody(String root);

  /// Explorer / workspace pages: confirm button and inline link that grants folder access to the host daemon
  ///
  /// In en, this message translates to:
  /// **'Grant'**
  String get explorerGrant;

  /// Explorer: snackbar after granting access
  ///
  /// In en, this message translates to:
  /// **'Workspace access granted'**
  String get explorerAccessGranted;

  /// Explorer / workspace pages: error snackbar when granting a root fails
  ///
  /// In en, this message translates to:
  /// **'Could not grant root: {error}'**
  String explorerGrantFailed(String error);

  /// Explorer toolbar: badge when the host daemon is not connected
  ///
  /// In en, this message translates to:
  /// **'Host offline'**
  String get explorerHostOffline;

  /// Explorer: empty state title
  ///
  /// In en, this message translates to:
  /// **'No workspace open'**
  String get explorerNoWorkspaceTitle;

  /// Explorer: empty state message
  ///
  /// In en, this message translates to:
  /// **'Open a folder from the welcome page to browse its files here.'**
  String get explorerNoWorkspaceMessage;

  /// Explorer: error title when the folder is not granted to the host daemon
  ///
  /// In en, this message translates to:
  /// **'Workspace access required'**
  String get explorerAccessRequiredTitle;

  /// Explorer: error title when listing a folder fails
  ///
  /// In en, this message translates to:
  /// **'Failed to load directory'**
  String get explorerLoadFailedTitle;

  /// Explorer: error body when the folder is not granted
  ///
  /// In en, this message translates to:
  /// **'This folder is outside every root granted to the host daemon. Grant access to browse it here and let the AURA agent work in it.'**
  String get explorerAccessRequiredBody;

  /// Explorer: button that grants the workspace root
  ///
  /// In en, this message translates to:
  /// **'Grant Access'**
  String get explorerGrantAccess;

  /// Explorer listing error: folder not granted
  ///
  /// In en, this message translates to:
  /// **'This folder is outside every root granted to the host daemon.'**
  String get explorerOutsideGrantedRoots;

  /// Explorer / host file browser listing error: daemon unreachable
  ///
  /// In en, this message translates to:
  /// **'The host daemon is unreachable.'**
  String get explorerHostUnreachable;

  /// Explorer listing error
  ///
  /// In en, this message translates to:
  /// **'Directory not found'**
  String get explorerDirectoryNotFound;

  /// Explorer: delete confirmation title (folder)
  ///
  /// In en, this message translates to:
  /// **'Delete folder?'**
  String get explorerDeleteFolderTitle;

  /// Explorer: delete confirmation title (file)
  ///
  /// In en, this message translates to:
  /// **'Delete file?'**
  String get explorerDeleteFileTitle;

  /// Explorer: delete confirmation body (folder)
  ///
  /// In en, this message translates to:
  /// **'\"{name}\" and everything inside it will be permanently deleted.'**
  String explorerDeleteFolderBody(String name);

  /// Explorer: delete confirmation body (file)
  ///
  /// In en, this message translates to:
  /// **'\"{name}\" will be permanently deleted.'**
  String explorerDeleteFileBody(String name);

  /// Explorer: rename dialog title (folder)
  ///
  /// In en, this message translates to:
  /// **'Rename folder'**
  String get explorerRenameFolderTitle;

  /// Explorer: rename dialog title (file)
  ///
  /// In en, this message translates to:
  /// **'Rename file'**
  String get explorerRenameFileTitle;

  /// Explorer: rename dialog field label
  ///
  /// In en, this message translates to:
  /// **'New name'**
  String get explorerNewName;

  /// Explorer: rename dialog button and context-menu item
  ///
  /// In en, this message translates to:
  /// **'Rename'**
  String get explorerRename;

  /// Explorer: rename error snackbar
  ///
  /// In en, this message translates to:
  /// **'Could not rename: {error}'**
  String explorerRenameFailed(String error);

  /// Explorer: delete error snackbar
  ///
  /// In en, this message translates to:
  /// **'Could not delete: {error}'**
  String explorerDeleteFailed(String error);

  /// Explorer: new-file dialog title
  ///
  /// In en, this message translates to:
  /// **'New file'**
  String get explorerNewFileTitle;

  /// Explorer: new-file dialog field label
  ///
  /// In en, this message translates to:
  /// **'File name'**
  String get explorerFileName;

  /// Explorer / Git panel: create button in name dialogs
  ///
  /// In en, this message translates to:
  /// **'Create'**
  String get explorerCreate;

  /// Explorer: new-file error snackbar
  ///
  /// In en, this message translates to:
  /// **'Could not create file: {error}'**
  String explorerCreateFileFailed(String error);

  /// Explorer: new-folder dialog title
  ///
  /// In en, this message translates to:
  /// **'New folder'**
  String get explorerNewFolderTitle;

  /// Explorer: new-folder dialog field label
  ///
  /// In en, this message translates to:
  /// **'Folder name'**
  String get explorerFolderName;

  /// Explorer: new-folder error snackbar
  ///
  /// In en, this message translates to:
  /// **'Could not create folder: {error}'**
  String explorerCreateFolderFailed(String error);

  /// Explorer context menu: open file
  ///
  /// In en, this message translates to:
  /// **'Open'**
  String get explorerMenuOpen;

  /// Explorer context menu
  ///
  /// In en, this message translates to:
  /// **'Copy path'**
  String get explorerMenuCopyPath;

  /// Explorer context menu
  ///
  /// In en, this message translates to:
  /// **'Copy relative path'**
  String get explorerMenuCopyRelativePath;

  /// Explorer context menu: show in the OS file manager
  ///
  /// In en, this message translates to:
  /// **'Reveal in filesystem'**
  String get explorerMenuReveal;

  /// Explorer context menu
  ///
  /// In en, this message translates to:
  /// **'Open in Terminal'**
  String get explorerMenuOpenInTerminal;

  /// Explorer context menu: collapse folder
  ///
  /// In en, this message translates to:
  /// **'Collapse'**
  String get explorerMenuCollapse;

  /// Explorer context menu: expand folder
  ///
  /// In en, this message translates to:
  /// **'Expand'**
  String get explorerMenuExpand;

  /// Explorer context menu
  ///
  /// In en, this message translates to:
  /// **'New File'**
  String get explorerMenuNewFile;

  /// Explorer context menu
  ///
  /// In en, this message translates to:
  /// **'New Folder'**
  String get explorerMenuNewFolder;

  /// Explorer tree: subfolder not granted to the host daemon
  ///
  /// In en, this message translates to:
  /// **'Access required'**
  String get explorerAccessRequired;

  /// Explorer tree: subfolder listing failed
  ///
  /// In en, this message translates to:
  /// **'Failed to load'**
  String get explorerLoadFailed;

  /// Explorer tree: empty folder
  ///
  /// In en, this message translates to:
  /// **'Empty'**
  String get explorerEmpty;

  /// Explorer: snackbar after copying a path
  ///
  /// In en, this message translates to:
  /// **'Path copied'**
  String get explorerPathCopied;

  /// Explorer: snackbar on non-Windows platforms
  ///
  /// In en, this message translates to:
  /// **'Reveal in filesystem is only supported on Windows for now'**
  String get explorerRevealWindowsOnly;

  /// Explorer: error snackbar
  ///
  /// In en, this message translates to:
  /// **'Could not open filesystem: {error}'**
  String explorerRevealFailed(String error);

  /// Git diff page: warning when the diff was cut short
  ///
  /// In en, this message translates to:
  /// **'Diff truncated -- view the full diff in a terminal.'**
  String get gitDiffTruncated;

  /// Git panel: merge branch picker title
  ///
  /// In en, this message translates to:
  /// **'Merge branch into current'**
  String get gitMergeDialogTitle;

  /// Git panel: remote-tracking branch entry
  ///
  /// In en, this message translates to:
  /// **'{name} (remote)'**
  String gitRemoteBranch(String name);

  /// Git panel: create-branch dialog title
  ///
  /// In en, this message translates to:
  /// **'Create branch'**
  String get gitCreateBranchTitle;

  /// Git panel: create-branch dialog field hint
  ///
  /// In en, this message translates to:
  /// **'Branch name'**
  String get gitBranchNameHint;

  /// Git panel: no workspace open
  ///
  /// In en, this message translates to:
  /// **'Open a workspace to use Source Control.'**
  String get gitNoWorkspace;

  /// Git panel: workspace not granted to the host daemon
  ///
  /// In en, this message translates to:
  /// **'Grant this workspace to the host daemon to use Source Control.'**
  String get gitGrantRequired;

  /// Git panel: fetch button tooltip
  ///
  /// In en, this message translates to:
  /// **'Fetch from all remotes'**
  String get gitFetchTooltip;

  /// Git panel: pull button tooltip
  ///
  /// In en, this message translates to:
  /// **'Pull (fast-forward only)'**
  String get gitPullTooltip;

  /// Git panel: push button tooltip
  ///
  /// In en, this message translates to:
  /// **'Push current branch to its upstream'**
  String get gitPushTooltip;

  /// Git panel: merge button
  ///
  /// In en, this message translates to:
  /// **'Merge'**
  String get gitMerge;

  /// Git panel: new-branch button
  ///
  /// In en, this message translates to:
  /// **'Branch'**
  String get gitBranch;

  /// Git panel: commit message field hint
  ///
  /// In en, this message translates to:
  /// **'Commit message'**
  String get gitCommitMessageHint;

  /// Git panel: commit button while an operation runs
  ///
  /// In en, this message translates to:
  /// **'Working...'**
  String get gitWorking;

  /// Git panel: commit button
  ///
  /// In en, this message translates to:
  /// **'Commit'**
  String get gitCommit;

  /// Git panel: commit history section
  ///
  /// In en, this message translates to:
  /// **'History'**
  String get gitHistory;

  /// Git panel: empty commit history
  ///
  /// In en, this message translates to:
  /// **'No commits loaded.'**
  String get gitNoCommits;

  /// Git panel: staged files section title
  ///
  /// In en, this message translates to:
  /// **'Staged Changes'**
  String get gitStagedChanges;

  /// Git panel: unstage every staged file
  ///
  /// In en, this message translates to:
  /// **'Unstage All'**
  String get gitUnstageAll;

  /// Git panel: unstage one file (tooltip)
  ///
  /// In en, this message translates to:
  /// **'Unstage'**
  String get gitUnstage;

  /// Git panel: unstaged files section title
  ///
  /// In en, this message translates to:
  /// **'Changes'**
  String get gitChanges;

  /// Git panel: stage every changed file
  ///
  /// In en, this message translates to:
  /// **'Stage All'**
  String get gitStageAll;

  /// Git panel: stage one file (tooltip)
  ///
  /// In en, this message translates to:
  /// **'Stage'**
  String get gitStage;

  /// Git panel: clean working tree
  ///
  /// In en, this message translates to:
  /// **'No changes.'**
  String get gitNoChanges;

  /// Git panel: branch selector when HEAD is detached
  ///
  /// In en, this message translates to:
  /// **'(detached HEAD)'**
  String get gitDetachedHead;

  /// Git panel: conflict banner
  ///
  /// In en, this message translates to:
  /// **'{count, plural, =1{1 file has merge conflicts} other{{count} files have merge conflicts}}'**
  String gitConflictCount(int count);

  /// Git panel: conflict banner button
  ///
  /// In en, this message translates to:
  /// **'Abort Merge'**
  String get gitAbortMerge;

  /// Git panel: stage a conflicted file as resolved
  ///
  /// In en, this message translates to:
  /// **'Mark Resolved'**
  String get gitMarkResolved;

  /// Git panel error: commit with nothing staged
  ///
  /// In en, this message translates to:
  /// **'Nothing is staged to commit.'**
  String get gitNothingStaged;

  /// Git panel error: checkout blocked by local changes
  ///
  /// In en, this message translates to:
  /// **'Switching branches would overwrite local changes.'**
  String get gitCheckoutOverwrite;

  /// Git panel error: checkout blocked by local changes in the listed files
  ///
  /// In en, this message translates to:
  /// **'Switching branches would overwrite local changes in: {files}'**
  String gitCheckoutOverwriteIn(String files);

  /// Git panel error (daemon reason no_upstream)
  ///
  /// In en, this message translates to:
  /// **'The current branch has no upstream set.'**
  String get gitNoUpstream;

  /// Git panel error (daemon reason diverged)
  ///
  /// In en, this message translates to:
  /// **'The branch has diverged -- pull (or push) cannot fast-forward.'**
  String get gitDiverged;

  /// Git panel error (daemon reason local_changes_would_be_overwritten)
  ///
  /// In en, this message translates to:
  /// **'Local changes would be overwritten.'**
  String get gitLocalChangesOverwritten;

  /// Git panel error (local_changes_would_be_overwritten) with the affected files
  ///
  /// In en, this message translates to:
  /// **'Local changes would be overwritten in: {files}'**
  String gitLocalChangesOverwrittenIn(String files);

  /// Git panel error: any other pull/push failure
  ///
  /// In en, this message translates to:
  /// **'Remote sync failed.'**
  String get gitRemoteSyncFailed;

  /// Desktop IDE welcome page subtitle
  ///
  /// In en, this message translates to:
  /// **'Open a folder to start coding with AURA'**
  String get workspaceWelcomeSubtitle;

  /// Desktop IDE welcome / workspace pages: open folder button
  ///
  /// In en, this message translates to:
  /// **'Open Folder'**
  String get workspaceOpenFolder;

  /// Desktop IDE welcome page: recent workspaces divider
  ///
  /// In en, this message translates to:
  /// **'Recent'**
  String get workspaceRecent;

  /// Workspace pages: grant-access dialog body after opening a folder
  ///
  /// In en, this message translates to:
  /// **'This allows the AURA agent to read/write files in \"{path}\".'**
  String workspaceGrantBody(String path);

  /// Workspace pages: decline granting access
  ///
  /// In en, this message translates to:
  /// **'Not Now'**
  String get workspaceNotNow;

  /// Desktop IDE welcome page: empty recent list
  ///
  /// In en, this message translates to:
  /// **'No recent workspaces'**
  String get workspaceNoRecent;

  /// Desktop IDE welcome page: empty recent list hint
  ///
  /// In en, this message translates to:
  /// **'Open a folder to get started'**
  String get workspaceNoRecentHint;

  /// Workspace pages: remove a workspace from the recent list (tooltip)
  ///
  /// In en, this message translates to:
  /// **'Remove from recent'**
  String get workspaceRemoveFromRecent;

  /// Workspace manager page title
  ///
  /// In en, this message translates to:
  /// **'Workspaces'**
  String get workspaceManagerTitle;

  /// Workspace manager page heading
  ///
  /// In en, this message translates to:
  /// **'Recent Workspaces'**
  String get workspaceRecentWorkspaces;

  /// Workspace manager: badge on the active workspace
  ///
  /// In en, this message translates to:
  /// **'Active'**
  String get workspaceActive;

  /// Workspace manager: when a workspace was last opened; date is preformatted
  ///
  /// In en, this message translates to:
  /// **'Last opened: {date}'**
  String workspaceLastOpened(String date);

  /// Workspace manager: open a workspace
  ///
  /// In en, this message translates to:
  /// **'Open'**
  String get workspaceOpen;

  /// Workspace manager: remove confirmation title
  ///
  /// In en, this message translates to:
  /// **'Remove workspace?'**
  String get workspaceRemoveTitle;

  /// Workspace manager: remove confirmation body
  ///
  /// In en, this message translates to:
  /// **'This will remove \"{name}\" from your recent workspaces.'**
  String workspaceRemoveBody(String name);

  /// Workspace manager: remove confirmation button
  ///
  /// In en, this message translates to:
  /// **'Remove'**
  String get workspaceRemove;

  /// Workspace manager: empty state title
  ///
  /// In en, this message translates to:
  /// **'No workspaces yet'**
  String get workspaceEmptyTitle;

  /// Workspace manager: empty state message
  ///
  /// In en, this message translates to:
  /// **'Open a folder to create your first workspace'**
  String get workspaceEmptyMessage;
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
      <String>['ar', 'en'].contains(locale.languageCode);

  @override
  bool shouldReload(_AppLocalizationsDelegate old) => false;
}

AppLocalizations lookupAppLocalizations(Locale locale) {
  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'ar':
      return AppLocalizationsAr();
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
