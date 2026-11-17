// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class AppLocalizationsEn extends AppLocalizations {
  AppLocalizationsEn([String locale = 'en']) : super(locale);

  @override
  String get greetingMorning => 'Good morning';

  @override
  String get greetingAfternoon => 'Good afternoon';

  @override
  String get greetingEvening => 'Good evening';

  @override
  String get captureBarHint => 'Ask AURA anything, or capture a task…';

  @override
  String get dueToday => 'Due today';

  @override
  String get viewAll => 'View all';

  @override
  String get nothingDueToday => 'Nothing due today';

  @override
  String get allCaughtUp => 'You\'re all caught up.';

  @override
  String get couldNotLoadDueToday => 'Couldn\'t load today\'s items';

  @override
  String get activeTasks => 'Active tasks';

  @override
  String get waterToday => 'Water today';

  @override
  String get spentThisPeriod => 'Spent this period';

  @override
  String get aiSystemStatus => 'AI system status';

  @override
  String get serverInference => 'AURA AI service';

  @override
  String get statusReady => 'Ready';

  @override
  String get statusUnavailable => 'Unavailable';

  @override
  String get localData => 'Local data';

  @override
  String get encryptedAesGcm => 'Encrypted (AES-256-GCM)';

  @override
  String get settingsTooltip => 'You';

  @override
  String get navToday => 'Today';

  @override
  String get navCopilot => 'Copilot';

  @override
  String get navLifeHub => 'Life Hub';

  @override
  String get navYou => 'You';

  @override
  String get lifeHubTitle => 'Life Hub';

  @override
  String get youTitle => 'You';

  @override
  String get knowledgeTitle => 'Knowledge';

  @override
  String get memoryTitle => 'Memory';

  @override
  String get feedbackTitle => 'Feedback';

  @override
  String get appearanceTitle => 'Appearance';

  @override
  String get engineStatusTitle => 'Engine status';

  @override
  String get onboardingNext => 'Next';

  @override
  String get onboardingCopilotTitle => 'AURA is your copilot';

  @override
  String get onboardingCopilotBody =>
      'Talk to it. It remembers, searches your documents, and proposes actions — you confirm the writes.';

  @override
  String get onboardingPrivacyTitle => 'Your private data stays with AURA';

  @override
  String get onboardingPrivacyBody =>
      'Nothing is processed on this phone. Your documents, memories and files are only read by AI models AURA runs itself. Other messages may be answered by an approved hosted AI provider, if the server’s operator allows it.';

  @override
  String get onboardingTourTitle => 'Today, Copilot, Life Hub, You';

  @override
  String get onboardingTourBody =>
      'Capture from anywhere. Browse tasks, calendar, finance, and health in Life Hub. Account tools live under You.';

  @override
  String hostdPairedToOtherServer(String appBackendUrl) {
    return 'This app is signed in to $appBackendUrl. Pair again to move the daemon there.';
  }

  @override
  String get onboardingSkip => 'Skip';

  @override
  String get onboardingGetStarted => 'Get started';

  @override
  String get notificationsTooltip => 'Notifications';

  @override
  String unreadNotifications(int count) {
    return '$count unread notifications';
  }

  @override
  String get settingsSectionAccount => 'Account';

  @override
  String get settingsSectionDesktop => 'Desktop';

  @override
  String get settingsSectionYourAura => 'Your AURA';

  @override
  String get settingsSectionEngineer => 'Engineer';

  @override
  String get settingsSectionSystem => 'System';

  @override
  String get settingsSectionAbout => 'About';

  @override
  String get settingsProfileTitle => 'Profile';

  @override
  String get settingsProfileSubtitle => 'Signed-in account and security';

  @override
  String get settingsAppearanceSubtitle => 'System, light, or dark';

  @override
  String get serverEnvironmentTitle => 'Server environment';

  @override
  String get settingsServerEnvironmentSubtitle =>
      'Production or development backend';

  @override
  String get settingsDevicesSubtitle =>
      'Desktop sign-ins and paired Engineer daemons';

  @override
  String get devicesTitle => 'Desktop devices';

  @override
  String get devicesIntro =>
      'Every desktop app signed in to your account and every paired Engineer daemon. Revoke one to sign it out at once.';

  @override
  String get devicesEmpty => 'No desktop devices';

  @override
  String get devicesEmptyMessage =>
      'Sign in on the desktop app or pair an Engineer daemon and it will appear here.';

  @override
  String get devicesLoadFailed => 'Couldn\'t load your devices';

  @override
  String get devicesRetry => 'Try again';

  @override
  String get devicesUnnamed => 'Unnamed device';

  @override
  String get devicesKindApp => 'Desktop app';

  @override
  String get devicesKindDaemon => 'Engineer daemon';

  @override
  String get devicesKindUnknown => 'Device';

  @override
  String devicesPairedOn(String date) {
    return 'since $date';
  }

  @override
  String get devicesThisDevice => 'this device';

  @override
  String get devicesRevoke => 'Revoke';

  @override
  String get devicesKeep => 'Keep';

  @override
  String get devicesRevokeConfirmTitle => 'Revoke this device?';

  @override
  String devicesRevokeConfirmMessage(String name) {
    return '$name will be signed out immediately and will have to sign in or pair again.';
  }

  @override
  String get devicesRevoked => 'Device revoked.';

  @override
  String get devicesRevokeFailed => 'Couldn\'t revoke that device. Try again.';

  @override
  String get settingsKnowledgeSubtitle => 'Documents Copilot can search';

  @override
  String get settingsMemorySubtitle => 'Typed facts AURA remembers';

  @override
  String get settingsFeedbackSubtitle => 'Corrections you’ve sent';

  @override
  String get settingsEngineerTitle => 'Engineer';

  @override
  String get settingsEngineerSubtitle => 'Workspaces, PRs, and sandbox runs';

  @override
  String get settingsGithubTitle => 'GitHub Connection';

  @override
  String get settingsGithubSubtitle => 'Connect your own GitHub account';

  @override
  String get settingsEngineStatusSubtitle =>
      'Whether AURA’s server is reachable';

  @override
  String get settingsAutomationsTitle => 'Automations';

  @override
  String get settingsAutomationsSubtitle => 'Gmail, GitHub, and Drive helpers';

  @override
  String get settingsUsageTitle => 'Usage on this device';

  @override
  String get settingsUsageSubtitle => 'Local latency and request counts';

  @override
  String get settingsDesktopIdeTitle => 'Desktop IDE';

  @override
  String get settingsDesktopIdeOn => 'AURA opens the IDE shell on this machine';

  @override
  String get settingsDesktopIdeOff =>
      'AURA opens the regular Home screen on this machine';

  @override
  String get settingsAboutTitle => 'Where AURA runs';

  @override
  String get settingsAboutBody =>
      'Requests are processed on AURA’s servers, never on this device. Your documents, memories, files and tool results are only read by AI models AURA runs itself. If the server’s operator turns it on, your other messages may be answered by an approved hosted AI provider.';

  @override
  String get themeSystem => 'System';

  @override
  String get themeLight => 'Light';

  @override
  String get themeDark => 'Dark';

  @override
  String get themeSystemSubtitle => 'Match the device setting';

  @override
  String get themeLightSubtitle => 'Always light';

  @override
  String get themeDarkSubtitle => 'Always dark';

  @override
  String get serverSwitchedSignInAgain => 'Sign in again to continue.';

  @override
  String get serverCurrent => 'Current server';

  @override
  String get serverReachable => 'Reachable';

  @override
  String get serverUnreachable => 'Unreachable';

  @override
  String get serverChecking => 'Checking…';

  @override
  String get serverReachabilityError => 'Could not check reachability';

  @override
  String get deleteAccountTitle => 'Delete account';

  @override
  String get deleteAccountSubtitle =>
      'Permanently erase your account and its data';

  @override
  String get deleteAccountConfirmTitle => 'Delete your account?';

  @override
  String get deleteAccountConfirmBody =>
      'This permanently erases your AURA account from the server: memories, documents, tasks and other synced records, conversations, and every signed-in device. It cannot be undone. AURA then signs you out and clears its data from this device. To leave without deleting anything, sign out instead.';

  @override
  String get deleteAccountConfirm => 'Delete';

  @override
  String get deleteAccountDone => 'Your account has been deleted.';

  @override
  String get deleteAccountPartial =>
      'Your sessions were ended, but part of your account could not be erased yet. Sign in and choose Delete account again to finish.';

  @override
  String get deleteAccountFailed => 'Your account was not deleted.';

  @override
  String get serverSwitchConfirmTitle => 'Switch server?';

  @override
  String get serverSwitchConfirmBody =>
      'Your account and data live on the server you are leaving. AURA uploads unsynced changes there first when it can, signs you out, and clears its data from this device. Sign in again on the new server.';

  @override
  String get serverSwitchConfirm => 'Switch';

  @override
  String get serverSwitchExplainer =>
      'Switching servers clears your session on this device. Sign in again after changing environments.';

  @override
  String get appName => 'AURA AI';

  @override
  String get cancel => 'Cancel';

  @override
  String get signOut => 'Sign out';

  @override
  String get signOutConfirmTitle => 'Sign out?';

  @override
  String get signOutConfirmBody =>
      'AURA uploads unsynced changes first when it can reach the server, then clears its data from this device. Changes it cannot upload are lost.';

  @override
  String get loginBiometricReason =>
      'Authenticate to unlock your AURA AI profile';

  @override
  String get loginTagline => 'Personal AI Operating System';

  @override
  String get loginWelcomeBack => 'Welcome back';

  @override
  String get loginSubtitle =>
      'Sign in to sync your schedules, studies, workspace, and agents.';

  @override
  String get loginSessionExpired =>
      'Your session expired. Sign in again to pick up where you left off — everything on this device is still here.';

  @override
  String get loginGoogle => 'Sign in with Google';

  @override
  String get loginGoogleBrowser => 'Sign in with Google (browser)';

  @override
  String get loginUnlockBiometrics => 'Unlock with biometrics';

  @override
  String get loginShowDeveloperPanel => 'Developer / offline access';

  @override
  String get loginHideDeveloperPanel => 'Hide developer panel';

  @override
  String get loginDeveloperName => 'Developer name';

  @override
  String get loginDeveloperNameHint => 'Enter name';

  @override
  String get loginDeveloperEmail => 'Developer email';

  @override
  String get loginDeveloperEmailHint => 'Enter email';

  @override
  String get loginDeveloperAccess => 'Access developer environment';

  @override
  String get profileTitle => 'User Profile & Security';

  @override
  String get profileNotSignedIn => 'Not signed in';

  @override
  String get profileShowAttestation => 'Show Attestation Status';

  @override
  String get add => 'Add';

  @override
  String get close => 'Close';

  @override
  String get retry => 'Retry';

  @override
  String get moreOptions => 'More';

  @override
  String get chatLoadEarlier => 'Load earlier messages';

  @override
  String get captureTitle => 'Ask Copilot';

  @override
  String get captureHint => 'Capture a thought, task, or question…';

  @override
  String get captureSend => 'Send to Copilot';

  @override
  String get chatToggleKnowledge => 'Knowledge';

  @override
  String get chatToggleMemory => 'Memory';

  @override
  String get chatToggleTools => 'Tools';

  @override
  String chatComposerWaiting(String persona) {
    return 'Waiting for $persona...';
  }

  @override
  String chatComposerHintShort(String persona) {
    return 'Ask $persona...';
  }

  @override
  String chatComposerHint(String persona) {
    return 'Ask $persona anything...';
  }

  @override
  String get chatStopGenerating => 'Stop generating';

  @override
  String get chatSend => 'Send';

  @override
  String get chatOpenSessions => 'Chat sessions';

  @override
  String get chatLiveVoice => 'Live voice';

  @override
  String get chatImageLimitReached => 'Image limit reached';

  @override
  String get chatAttachImage => 'Attach image';

  @override
  String chatAttachedImageLabel(int index, int count, String name) {
    return 'Attached image $index of $count: $name';
  }

  @override
  String chatRemoveImage(int index) {
    return 'Remove image $index';
  }

  @override
  String chatSentImageLabel(int index, int count, String name) {
    return 'Sent image $index of $count: $name. Open preview';
  }

  @override
  String get chatClosePreview => 'Close preview';

  @override
  String get chatNewSession => 'New Session';

  @override
  String get chatSessionsHeader => 'CHAT SESSIONS';

  @override
  String get chatDefaultSession => 'Default Workspace';

  @override
  String chatSessionLabel(String id) {
    return 'Session $id';
  }

  @override
  String get chatDeleteConversation => 'Delete conversation';

  @override
  String get chatConversationDeleted => 'Conversation deleted';

  @override
  String get chatNoReplyTitle => 'No reply this turn';

  @override
  String get chatNoReplyBody =>
      'AURA finished without an answer. Retry to send your last message again.';

  @override
  String get chatEmptyTitle => 'Ask Copilot Anything';

  @override
  String get chatEmptyMessage =>
      'Programming questions, academic inquiries, or coordinating your tasks.';

  @override
  String get chatSuggestExplainTitle => 'Explain a Concept';

  @override
  String get chatSuggestExplainSubtitle =>
      'Ask a programming or academic question';

  @override
  String get chatSuggestExplainPrompt =>
      'Explain how dependency injection works';

  @override
  String get chatSuggestHealthTitle => 'Log Health';

  @override
  String get chatSuggestHealthSubtitle =>
      'Log water intake and exercise duration for today';

  @override
  String get chatSuggestHealthPrompt =>
      'Log 500ml of water and 30 minutes of exercise';

  @override
  String get chatSuggestFinanceTitle => 'Financial Health';

  @override
  String get chatSuggestFinanceSubtitle =>
      'Show my current balance and expense distribution';

  @override
  String get chatSuggestFinancePrompt => 'How are my expenses this month?';

  @override
  String get chatSuggestMemoryTitle => 'Agent Memory';

  @override
  String get chatSuggestMemorySubtitle =>
      'View facts the system has remembered about me';

  @override
  String get chatSuggestMemoryPrompt => 'What do you remember about me?';

  @override
  String get chatRateAnswer => 'Rate this answer';

  @override
  String chatRatingStar(int value, int max) {
    return '$value of $max';
  }

  @override
  String get chatFeedbackCorrection => 'Optional correction';

  @override
  String get chatFeedbackSend => 'Send feedback';

  @override
  String get chatFeedbackSent => 'Thanks — feedback sent.';

  @override
  String get memoryBankTitle => 'User Memory Bank';

  @override
  String get memoryBankExplainer =>
      'These are facts and preferences AURA has learned about you. They are injected as system prompts to personalize reasoning.';

  @override
  String get memoryBankAddHint => 'Add a custom fact manually...';

  @override
  String get memoryBankEmpty =>
      'No memories stored yet. AURA extracts them automatically from conversations, or you can add one above.';

  @override
  String get memoryBankDelete => 'Delete memory';

  @override
  String get chatCloudUnavailableTitle => 'Cloud AI is currently unavailable';

  @override
  String get chatCloudUnavailableBody =>
      'AURA could not connect to the cloud assistant. You can retry or report this issue.';

  @override
  String get chatReportIssue => 'Report Issue';

  @override
  String get pendingActionsTitle => 'AURA Proposes Actions';

  @override
  String get pendingActionsReject => 'Reject';

  @override
  String get pendingActionsApprove => 'Approve Actions';

  @override
  String get pendingActionsAllSucceeded =>
      'Proposed actions successfully executed!';

  @override
  String pendingActionsAllFailed(String labels) {
    return 'All actions failed: $labels';
  }

  @override
  String pendingActionsSomeFailed(int count, String labels) {
    return '$count action(s) failed: $labels';
  }

  @override
  String get voiceMicPermission =>
      'Microphone permission is needed to record a voice note.';

  @override
  String get voiceRecordingUnavailable =>
      'Voice recording is not available on this device.';

  @override
  String get voiceTranscribeFailed => 'Could not transcribe that voice note.';

  @override
  String get voiceReadFailed => 'Could not read that recording.';

  @override
  String get voiceStopRecording => 'Stop recording';

  @override
  String get voiceRecordNote => 'Record a voice note';

  @override
  String get voiceStopPlayback => 'Stop';

  @override
  String get voiceListen => 'Listen to this reply';

  @override
  String get engineerTaskHistory => 'Engineer Task History';

  @override
  String get sandboxNoChanges => '(no changes)';

  @override
  String get stateToneError => 'Error';

  @override
  String get stateToneWarning => 'Warning';

  @override
  String get stateToneOffline => 'Offline';

  @override
  String get commandPaletteLabel => 'Command palette';

  @override
  String get commandPaletteHint => 'Type a command...';

  @override
  String get commandPaletteNoMatchesTitle => 'No matching commands';

  @override
  String commandPaletteNoMatchesBody(String query) {
    return 'Nothing here matches \"$query\". Try a shorter word, or clear the search to see everything.';
  }

  @override
  String get commandPaletteClearSearch => 'Clear search';

  @override
  String get refresh => 'Refresh';

  @override
  String get changeDate => 'Change date';

  @override
  String get clearDueDate => 'Clear due date';

  @override
  String get taskMarkDone => 'Mark as done';

  @override
  String get taskMarkNotDone => 'Mark as not done';

  @override
  String get hostdBrowseExecutable => 'Browse for the daemon executable';

  @override
  String get hostdSaveExecutable => 'Save executable path';

  @override
  String get liveVoiceEnd => 'End live voice';

  @override
  String get eventChangeStart => 'Change start time';

  @override
  String get eventChangeEnd => 'Change end time';

  @override
  String get eventAdd => 'Add event';

  @override
  String get eventDelete => 'Delete event';

  @override
  String get calendarPreviousMonth => 'Previous month';

  @override
  String get calendarNextMonth => 'Next month';

  @override
  String get githubImportSubtask => 'Import as coding task';

  @override
  String get chatActivePersonaHeader => 'ACTIVE PERSONA';

  @override
  String get chatUseKnowledge => 'Use Knowledge (RAG)';

  @override
  String get chatRoutingHeader => 'AI ROUTING';

  @override
  String get chatAiEngine => 'AI engine';

  @override
  String get chatIssueRecorded => 'Issue recorded in local telemetry.';

  @override
  String chatPersonaFallback(String resolved, String requested) {
    return 'This request was handled by $resolved, not $requested -- your account may not have access to $requested.';
  }

  @override
  String get chatAwaitingConfirmation =>
      'Confirm the action above to continue.';

  @override
  String get personaPickerTitle => 'Choose a persona';

  @override
  String get loginDeveloperTip =>
      'Tip: Google Sign-In failed. To test locally without registering SHA-1 fingerprints in the Google Console, use the \"Access Developer Environment\" bypass option above.';

  @override
  String planIncompleteHeadline(int total, int gaps) {
    String _temp0 = intl.Intl.pluralLogic(
      gaps,
      locale: localeName,
      other: 'AURA planned $total steps and $gaps did not complete.',
      one: 'AURA planned $total steps and one did not complete.',
    );
    return '$_temp0';
  }

  @override
  String get planIncompleteAnnouncement =>
      'This answer was given without them.';

  @override
  String planStepWithStatus(String step, String status) {
    return '$step — $status';
  }
}
