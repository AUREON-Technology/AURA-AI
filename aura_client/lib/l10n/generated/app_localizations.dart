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

  /// Sign in with Apple button on the login page
  ///
  /// In en, this message translates to:
  /// **'Continue with Apple'**
  String get loginApple;

  /// Opens the email and password form on the login page
  ///
  /// In en, this message translates to:
  /// **'Continue with email'**
  String get loginEmail;

  /// Email address field
  ///
  /// In en, this message translates to:
  /// **'Email'**
  String get emailFieldLabel;

  /// Password field
  ///
  /// In en, this message translates to:
  /// **'Password'**
  String get passwordFieldLabel;

  /// Password confirmation field when creating an account
  ///
  /// In en, this message translates to:
  /// **'Confirm password'**
  String get confirmPasswordFieldLabel;

  /// Display name field when creating an account
  ///
  /// In en, this message translates to:
  /// **'Name'**
  String get nameFieldLabel;

  /// Submits the email and password form
  ///
  /// In en, this message translates to:
  /// **'Sign in'**
  String get emailSignInAction;

  /// Submits the create-account form
  ///
  /// In en, this message translates to:
  /// **'Create account'**
  String get emailCreateAccountAction;

  /// Switches the email form to password reset
  ///
  /// In en, this message translates to:
  /// **'Forgot password?'**
  String get emailForgotPassword;

  /// Switches the email form to account creation
  ///
  /// In en, this message translates to:
  /// **'New to AURA? Create an account'**
  String get emailSwitchToRegister;

  /// Switches the email form back to sign-in
  ///
  /// In en, this message translates to:
  /// **'Already have an account? Sign in'**
  String get emailSwitchToSignIn;

  /// Submits the password reset form
  ///
  /// In en, this message translates to:
  /// **'Send reset link'**
  String get emailSendReset;

  /// Shown after a reset request; never says whether the account exists
  ///
  /// In en, this message translates to:
  /// **'If an account exists for that address, we\'ve sent a reset link.'**
  String get emailResetSent;

  /// Validation message for the email field
  ///
  /// In en, this message translates to:
  /// **'Enter a valid email address.'**
  String get emailInvalid;

  /// Validation message for a short password
  ///
  /// In en, this message translates to:
  /// **'Use at least 8 characters.'**
  String get passwordTooShort;

  /// Validation message when the confirmation differs
  ///
  /// In en, this message translates to:
  /// **'The passwords don\'t match.'**
  String get passwordsDontMatch;

  /// Shown until an email/password account verifies its address
  ///
  /// In en, this message translates to:
  /// **'Check your inbox to verify your email. You\'ll need it to create or join a team.'**
  String get verifyEmailBanner;

  /// Resends the verification email
  ///
  /// In en, this message translates to:
  /// **'Resend'**
  String get verifyEmailResend;

  /// Sign-in failure: wrong email or password
  ///
  /// In en, this message translates to:
  /// **'That email and password don\'t match an account.'**
  String get identityWrongCredentials;

  /// Account creation failure: email taken
  ///
  /// In en, this message translates to:
  /// **'An account already uses this email. Sign in instead.'**
  String get identityEmailInUse;

  /// Account creation failure: weak password
  ///
  /// In en, this message translates to:
  /// **'Choose a stronger password.'**
  String get identityWeakPassword;

  /// Sign-in failure: malformed email
  ///
  /// In en, this message translates to:
  /// **'That email address isn\'t valid.'**
  String get identityInvalidEmail;

  /// Sign-in failure: offline
  ///
  /// In en, this message translates to:
  /// **'Couldn\'t reach the sign-in service. Check your connection.'**
  String get identityNetwork;

  /// Sign-in failure: the email is registered with a different method
  ///
  /// In en, this message translates to:
  /// **'This email already uses another sign-in method. Sign in with that method instead.'**
  String get identityDifferentMethod;

  /// Sign-in failure: provider not configured or other error
  ///
  /// In en, this message translates to:
  /// **'This sign-in method isn\'t available right now.'**
  String get identityUnavailable;

  /// Re-checks the account after the user clicked the verification link
  ///
  /// In en, this message translates to:
  /// **'I\'ve verified'**
  String get verifyEmailDone;
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
