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

  @override
  String get languageSectionTitle => 'Language';

  @override
  String get languageSystem => 'System';

  @override
  String get languageSystemSubtitle => 'Use your device\'s language';

  @override
  String get deviceUnverifiedBanner =>
      'This device could not be verified. Some features may be unavailable.';

  @override
  String get commonDismiss => 'Dismiss';

  @override
  String get commonSave => 'Save';

  @override
  String get commonDelete => 'Delete';

  @override
  String get commonEdit => 'Edit';

  @override
  String get commonAdd => 'Add';

  @override
  String get commonOk => 'OK';

  @override
  String get commonConfirm => 'Confirm';

  @override
  String get commonUndo => 'Undo';

  @override
  String get commonBack => 'Back';

  @override
  String get commonNext => 'Next';

  @override
  String get commonDone => 'Done';

  @override
  String get commonCopy => 'Copy';

  @override
  String get commonCopied => 'Copied';

  @override
  String get commonRefresh => 'Refresh';

  @override
  String get commonSearch => 'Search';

  @override
  String get commonLoading => 'Loading…';

  @override
  String get commonSomethingWentWrong => 'Something went wrong';

  @override
  String get commonNone => 'None';

  @override
  String get commonYes => 'Yes';

  @override
  String get commonNo => 'No';

  @override
  String get healthPageTitle => 'Health Monitor';

  @override
  String get healthPageSubtitle =>
      'Track fitness parameters, log water, sleep, and review logs';

  @override
  String get healthDailyGoalsTitle => 'Daily Goals Progress';

  @override
  String healthWeightBadge(String weight) {
    return 'Weight: $weight kg';
  }

  @override
  String get healthWaterIntake => 'Water Intake';

  @override
  String get healthUnitMl => 'ml';

  @override
  String get healthSleepDuration => 'Sleep Duration';

  @override
  String get healthUnitMins => 'mins';

  @override
  String healthSleepHoursProgress(String hours) {
    return '$hours / 8.0 hrs';
  }

  @override
  String get healthExerciseDuration => 'Exercise Duration';

  @override
  String get healthCaloriesConsumed => 'Calories Consumed';

  @override
  String get healthUnitKcal => 'kcal';

  @override
  String get healthQuickLoggerTitle => 'Quick Logger Panel';

  @override
  String get healthAddWater250 => '+250ml Water';

  @override
  String get healthAddWater500 => '+500ml Water';

  @override
  String get healthLogSleepTitle => 'Log Sleep Duration';

  @override
  String get healthSleepMinutesLabel => 'Sleep Minutes';

  @override
  String get healthSleepMinutesHint => 'e.g. 480 for 8 hours';

  @override
  String get healthLogSleep => 'Log Sleep';

  @override
  String get healthLogExerciseTitle => 'Log Exercise Duration';

  @override
  String get healthExerciseMinutesLabel => 'Exercise Minutes';

  @override
  String healthExampleHint(String example) {
    return 'e.g. $example';
  }

  @override
  String get healthLogExercise => 'Log Exercise';

  @override
  String get healthLogCaloriesTitle => 'Log Calories';

  @override
  String get healthKcalConsumedLabel => 'Kcal Consumed';

  @override
  String get healthLogCalories => 'Log Calories';

  @override
  String get healthLogWeightTitle => 'Log Current Weight';

  @override
  String get healthWeightKgLabel => 'Weight (kg)';

  @override
  String get healthLogWeight => 'Log Weight';

  @override
  String get healthWeeklyTrendsTitle => 'Weekly Health Trends';

  @override
  String get healthWeeklyTrendsEmpty => 'Log metrics to populate weekly graphs';

  @override
  String get healthWaterChartTitle => 'Water Intake (ml)';

  @override
  String get healthWeightChartTitle => 'Weight Trends (kg)';

  @override
  String get healthWeightChartEmpty => 'Log weight to display trend line';

  @override
  String get knowledgeFileReadFailed => 'Could not read that file.';

  @override
  String get knowledgeAddDocument => 'Add a document';

  @override
  String get knowledgeTitleLabel => 'Title';

  @override
  String get knowledgeContentLabel => 'Content';

  @override
  String get knowledgeTitleContentRequired => 'Title and content are required.';

  @override
  String get knowledgeUploading => 'Uploading…';

  @override
  String get knowledgeUploadPdfInstead => 'Upload a PDF instead';

  @override
  String get knowledgeSearchLabel => 'Look up a passage';

  @override
  String get knowledgeSearchEmpty =>
      'Searched your library — nothing relevant turned up.';

  @override
  String get knowledgeCagTitle => 'Always in context (CAG)';

  @override
  String knowledgeCagBudget(int tokens, int budget) {
    return 'Budget $tokens / $budget tokens';
  }

  @override
  String get knowledgeCagNothingPinned => 'nothing pinned yet';

  @override
  String get knowledgeCagHandbook => 'handbook';

  @override
  String get knowledgeCagYours => 'yours';

  @override
  String knowledgeCagDocTokens(String tokens) {
    return '$tokens tok';
  }

  @override
  String get knowledgeLibrary => 'Library';

  @override
  String get knowledgeLoadFailed => 'Could not load documents';

  @override
  String get knowledgeEmptyTitle => 'No documents yet';

  @override
  String get knowledgeEmptyMessage =>
      'Save a title and some text to build your library.';

  @override
  String get knowledgeAlwaysInContext => 'Always in context';

  @override
  String get knowledgeUnpinTooltip => 'Remove from always-in-context';

  @override
  String get knowledgePinTooltip => 'Keep always in context';

  @override
  String get knowledgeAddFailed => 'Could not add this document.';

  @override
  String knowledgePassagesHeldBack(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count passages were held back.',
      one: '1 passage was held back.',
    );
    return '$_temp0';
  }

  @override
  String get knowledgeSaved => 'Saved.';

  @override
  String get knowledgePdfUploadFailed => 'Could not upload this PDF.';

  @override
  String get knowledgeUploaded => 'Uploaded.';

  @override
  String get goalsWelcomeBack => 'Welcome back,';

  @override
  String get goalsDefaultUserName => 'Aura User';

  @override
  String get goalsPageTitle => 'Goals';

  @override
  String get goalsPageSubtitle => 'Track your aspirations and progress';

  @override
  String get goalsNewGoal => 'New Goal';

  @override
  String get goalsEmptyTitle => 'No goals yet';

  @override
  String get goalsEmptyMessage => 'Create your first goal to get started';

  @override
  String get goalsCreateGoalTitle => 'Create Goal';

  @override
  String get goalsTitleRequiredLabel => 'Title *';

  @override
  String get goalsDescriptionLabel => 'Description';

  @override
  String get goalsStatusLabel => 'Status';

  @override
  String get goalsPriorityLabel => 'Priority';

  @override
  String get goalsSetTargetDate => 'Set target date (optional)';

  @override
  String goalsTargetDate(String date) {
    return 'Target: $date';
  }

  @override
  String goalsProgressPercent(int percent) {
    return 'Progress: $percent%';
  }

  @override
  String get goalsCreate => 'Create';

  @override
  String get goalsStatusActive => 'active';

  @override
  String get goalsStatusAchieved => 'achieved';

  @override
  String get goalsStatusAbandoned => 'abandoned';

  @override
  String get goalsPriorityLow => 'low';

  @override
  String get goalsPriorityMedium => 'medium';

  @override
  String get goalsPriorityHigh => 'high';

  @override
  String get tasksWelcomeBack => 'Welcome back,';

  @override
  String get tasksDefaultUserName => 'Aura User';

  @override
  String get tasksPageTitle => 'Task Workspace';

  @override
  String get tasksPageSubtitle => 'Prioritize and manage your goals';

  @override
  String get tasksViewSyncLogs => 'View Sync Logs';

  @override
  String get tasksSyncing => 'Syncing...';

  @override
  String get tasksSyncFailed => 'Tasks sync encountered errors.';

  @override
  String get tasksSyncSucceeded => 'Google Tasks sync completed successfully!';

  @override
  String get tasksSyncButton => 'Sync Tasks';

  @override
  String get tasksAddTask => 'Add Task';

  @override
  String get tasksAddNewTaskTitle => 'Add New Task';

  @override
  String get tasksTitleLabel => 'Task Title';

  @override
  String get tasksTitleHint => 'What needs to be done?';

  @override
  String get tasksTitleRequired => 'Please enter a title';

  @override
  String get tasksDescriptionLabel => 'Description';

  @override
  String get tasksDescriptionHint => 'Add details (optional)';

  @override
  String get tasksCategoryLabel => 'Category';

  @override
  String get tasksPriorityLabel => 'Priority';

  @override
  String get tasksNoDueDate => 'No Due Date Selected';

  @override
  String get tasksSaveTask => 'Save Task';

  @override
  String get tasksCategoryAll => 'All';

  @override
  String get tasksCategoryPersonal => 'Personal';

  @override
  String get tasksCategoryAcademic => 'Academic';

  @override
  String get tasksCategoryClient => 'Client';

  @override
  String get tasksCategoryProject => 'Project';

  @override
  String get tasksPriorityLow => 'Low';

  @override
  String get tasksPriorityMedium => 'Medium';

  @override
  String get tasksPriorityHigh => 'High';

  @override
  String get tasksEmptyTitle => 'All caught up!';

  @override
  String get tasksEmptyMessage =>
      'Enjoy your day or create a new task to stay organized.';

  @override
  String get tasksSyncLogTitle => 'Tasks Sync Activity Log';

  @override
  String get tasksSyncLogEmpty => 'No logs recorded yet. Press sync to begin.';

  @override
  String get tasksDeleteTask => 'Delete task';

  @override
  String get tasksTaskDeleted => 'Task deleted';

  @override
  String get calendarSyncFailed => 'Calendar sync encountered errors.';

  @override
  String get calendarSyncSucceeded =>
      'Google Calendar sync completed successfully!';

  @override
  String get calendarPageTitle => 'Calendar';

  @override
  String get calendarPageSubtitle =>
      'Manage local schedules and sync with Google';

  @override
  String get calendarViewSyncLogs => 'View Sync Logs';

  @override
  String get calendarSyncing => 'Syncing...';

  @override
  String get calendarSyncButton => 'Sync Calendar';

  @override
  String get calendarSyncLogTitle => 'Sync Activity Log';

  @override
  String get calendarSyncLogEmpty =>
      'No logs recorded yet. Press sync to begin.';

  @override
  String get calendarCreateEventTitle => 'Create Calendar Event';

  @override
  String get calendarEventTitleLabel => 'Event Title';

  @override
  String get calendarEventTitleHint => 'e.g. Team Standup';

  @override
  String get calendarEventDescriptionLabel => 'Description (Optional)';

  @override
  String get calendarEventDescriptionHint => 'Enter event details';

  @override
  String get calendarStartTime => 'Start Time';

  @override
  String get calendarEndTime => 'End Time';

  @override
  String get calendarEventCreated => 'Event created successfully!';

  @override
  String get calendarCreate => 'Create';

  @override
  String get calendarNoEvents => 'No events scheduled for today';

  @override
  String get calendarCloudSynced => 'Cloud Synced';

  @override
  String get calendarDeleteEventTitle => 'Delete Event';

  @override
  String calendarDeleteEventConfirm(String title) {
    return 'Are you sure you want to delete \'$title\'?';
  }

  @override
  String get calendarEventDeleted => 'Event deleted successfully.';

  @override
  String get hostPanelTitle => 'Host Daemon';

  @override
  String get hostFullSettings => 'Full Settings';

  @override
  String hostStatusError(String error) {
    return 'Error: $error';
  }

  @override
  String get hostStatusGaveUp => 'Stopped after repeated crashes';

  @override
  String hostStatusRestarting(int seconds) {
    return 'Crashed, restarting in ${seconds}s...';
  }

  @override
  String get hostStatusNotRunning => 'Not running';

  @override
  String get hostStatusRunningNotPaired => 'Running, not paired';

  @override
  String get hostStatusRunningOtherServer =>
      'Running, paired to a different server';

  @override
  String get hostStatusRunningPaired => 'Running, paired';

  @override
  String get hostStatusReconnecting => 'Running, reconnecting...';

  @override
  String get hostStatusPairedUpdateDaemon =>
      'Running, paired (update aura_hostd)';

  @override
  String hostBackendLabel(String url) {
    return 'Backend: $url';
  }

  @override
  String hostOtherServerNotice(String url) {
    return 'This app is signed in to $url. Pair again to move the daemon there.';
  }

  @override
  String get hostStarting => 'Starting...';

  @override
  String get hostStartNow => 'Start now';

  @override
  String get hostStart => 'Start';

  @override
  String get hostCancelRestart => 'Cancel restart';

  @override
  String get hostStop => 'Stop';

  @override
  String get hostPairing => 'Pairing...';

  @override
  String get hostRepair => 'Re-pair';

  @override
  String get hostPair => 'Pair';

  @override
  String get hostGrantedRoots => 'Granted Roots';

  @override
  String get hostTrusted => 'Trusted';

  @override
  String get hostSetExecutableFirst =>
      'Set the aura_hostd executable path in Full Settings first.';

  @override
  String hostCouldNotStart(String error) {
    return 'Could not start aura_hostd: $error';
  }

  @override
  String hostCouldNotStop(String error) {
    return 'Could not stop aura_hostd: $error';
  }

  @override
  String get hostPairDialogTitle => 'Pair this device';

  @override
  String get hostPairApproving =>
      'Approving with your signed-in AURA session...';

  @override
  String get hostPairWaiting => 'Waiting for approval...';

  @override
  String get hostPairSignInFirst =>
      'Sign in to AURA first -- pairing needs a valid session to approve with.';

  @override
  String get hostPaired => 'Paired.';

  @override
  String hostPairingFailed(String reason) {
    return 'Pairing failed: $reason';
  }

  @override
  String get hostPairingExpired => 'Pairing code expired.';

  @override
  String hostCouldNotStartPairing(String error) {
    return 'Could not start pairing: $error';
  }

  @override
  String get debugOpenWorkspace => 'Open a workspace to use the Debug Console.';

  @override
  String get debugScriptHint => 'script.py (relative to workspace root)';

  @override
  String get debugStart => 'Start';

  @override
  String get debugContinue => 'Continue';

  @override
  String get debugStepOver => 'Step Over';

  @override
  String get debugStepInto => 'Step Into';

  @override
  String get debugStepOut => 'Step Out';

  @override
  String get debugStop => 'Stop';

  @override
  String get debugStatusIdle => 'Idle';

  @override
  String get debugStatusStarting => 'Starting...';

  @override
  String debugStatusRunningPid(int pid) {
    return 'Running (pid $pid)';
  }

  @override
  String get debugStatusRunning => 'Running';

  @override
  String get debugStatusPaused => 'Paused';

  @override
  String get debugStatusStopped => 'Stopped';

  @override
  String get debugBreakpointsLabel => 'Breakpoints:';

  @override
  String get debugLineHint => 'line #';

  @override
  String get debugCallStack => 'CALL STACK';

  @override
  String get debugVariables => 'VARIABLES';

  @override
  String get controlCenterDiscardTitle => 'Discard this task?';

  @override
  String controlCenterDiscardBody(String branch, String path) {
    return 'This permanently deletes the \"$branch\" branch and its worktree at $path. Any unmerged work in it is lost. This cannot be undone.';
  }

  @override
  String get controlCenterDiscard => 'Discard';

  @override
  String get controlCenterMergedTitle => 'Merged';

  @override
  String controlCenterMergedBody(String branch) {
    return '\"$branch\" was merged locally. Discard its now-merged worktree?';
  }

  @override
  String get controlCenterKeepIt => 'Keep it';

  @override
  String get controlCenterTitle => 'Agent Review';

  @override
  String get controlCenterEmpty =>
      'No isolated agent tasks right now. Starting a new chat task creates one automatically.';

  @override
  String get controlCenterNoChanges => '(no changes)';

  @override
  String controlCenterDiffStatError(String message) {
    return 'error: $message';
  }

  @override
  String get controlCenterBaseBranchUnknown =>
      'Could not determine the base branch to diff against.';

  @override
  String get controlCenterMergeConflict =>
      'Merge conflict -- resolve in the main worktree, then commit there directly.';

  @override
  String get controlCenterHideDiff => 'Hide diff';

  @override
  String get controlCenterViewDiff => 'View diff';

  @override
  String get controlCenterSquashMerge => 'Squash merge';

  @override
  String get controlCenterWorking => 'Working...';

  @override
  String get controlCenterMerge => 'Merge';

  @override
  String get terminalOpenWorkspace => 'Open a workspace to use the terminal.';

  @override
  String get terminalGrantWorkspace =>
      'Grant this workspace to the host daemon to use the terminal.';

  @override
  String get terminalUntrusted =>
      'Workspace is not trusted. Trust it from the Host panel to use the terminal.';

  @override
  String get terminalStarting => 'Starting terminal...';

  @override
  String terminalSessionEndedWithCode(int code) {
    return 'Session ended (exit code $code).';
  }

  @override
  String get terminalSessionEnded => 'Session ended.';

  @override
  String get terminalRestart => 'Restart';

  @override
  String get outputFilterAll => 'All';

  @override
  String get outputChannelAgent => 'Agent';

  @override
  String get outputChannelHost => 'Host';

  @override
  String get outputChannelTasks => 'Tasks';

  @override
  String get outputChannelDiagnostics => 'Diagnostics';

  @override
  String get outputClear => 'Clear Output';

  @override
  String get outputEmpty => 'No output yet';

  @override
  String get diagnosticsOpenWorkspace => 'Open a workspace to see problems.';

  @override
  String get diagnosticsDetecting => 'Detecting diagnostics tool...';

  @override
  String get diagnosticsNoTool =>
      'No diagnostics tool detected for this workspace.';

  @override
  String get diagnosticsRerun => 'Rerun';

  @override
  String get diagnosticsNoProblems => 'No problems detected';

  @override
  String diagnosticsToolNotFound(String tool) {
    return '\"$tool\" is not installed or not on PATH.';
  }

  @override
  String get editorNoFileOpen => 'No file open';

  @override
  String get editorNoFileOpenHint =>
      'Open a file from the Explorer or press Ctrl+P';

  @override
  String editorCouldNotRead(String path, String error) {
    return 'Could not read $path: $error';
  }

  @override
  String get editorNoActiveWorkspace => 'No active workspace';

  @override
  String get editorRestoredNotice =>
      'Restored unsaved changes from your last session.';

  @override
  String get editorRestoredOverChange =>
      'Restored unsaved changes from your last session. The file has changed on disk since; saving will overwrite that change.';

  @override
  String get editorUnsavedTitle => 'Unsaved changes';

  @override
  String editorUnsavedBody(String title) {
    return '\"$title\" has unsaved changes.';
  }

  @override
  String get editorDiscard => 'Discard';

  @override
  String get editorQuickOpenHint => 'Type to search files...';

  @override
  String get editorNoFilesFound => 'No files found';

  @override
  String get editorNoMatches => 'No matches';

  @override
  String agentWorkspaceLabel(String path) {
    return 'Workspace: $path';
  }

  @override
  String get agentLockedTitle => 'Engineer is not available on this account';

  @override
  String get agentLockedMessage =>
      'Engineer needs the engineer_agent capability. Ask an operator to grant it; until then, messages from here would be answered by the general assistant instead.';

  @override
  String get agentEmptyTitle => 'No messages yet';

  @override
  String get agentEmptyMessage =>
      'Ask Engineer about this workspace — it can read your files, run commands and propose changes for you to review before anything is applied.';

  @override
  String get agentTaskDefault => 'Default';

  @override
  String agentTaskLabel(String id) {
    return 'Task $id';
  }

  @override
  String get agentSwitchTask => 'Switch task';

  @override
  String get agentNewTask => '+ New Task';

  @override
  String get engineeringDaemonSaved => 'Saved.';

  @override
  String get engineeringDaemonPathMissing =>
      'Set the aura_hostd executable path first.';

  @override
  String engineeringDaemonStartFailed(String error) {
    return 'Could not start aura_hostd: $error';
  }

  @override
  String engineeringDaemonStopFailed(String error) {
    return 'Could not stop aura_hostd: $error';
  }

  @override
  String get engineeringPairThisDevice => 'Pair this device';

  @override
  String get engineeringPairApproving =>
      'Approving with your signed-in AURA session...';

  @override
  String get engineeringPairWaiting => 'Waiting for approval...';

  @override
  String get engineeringPairSignInFirst =>
      'Sign in to AURA first -- pairing needs a valid session to approve with.';

  @override
  String engineeringPairApprovalRejected(int status, String body) {
    return 'Approval rejected: HTTP $status $body';
  }

  @override
  String get engineeringPaired => 'Paired.';

  @override
  String engineeringPairedNamed(String name) {
    return 'Paired $name.';
  }

  @override
  String engineeringPairingFailed(String reason) {
    return 'Pairing failed: $reason';
  }

  @override
  String get engineeringPairingExpired => 'Pairing code expired.';

  @override
  String engineeringPairingStartFailed(String error) {
    return 'Could not start pairing: $error';
  }

  @override
  String engineeringGrantRootFailed(String path, String error) {
    return 'Could not grant $path: $error';
  }

  @override
  String get engineeringRevokeRootTitle => 'Revoke workspace root?';

  @override
  String engineeringRevokeRootBody(String path) {
    return 'The agent will no longer be able to read or write anything under $path.';
  }

  @override
  String get engineeringRevoke => 'Revoke';

  @override
  String get engineeringDaemonTitle => 'Desktop Engineer Daemon';

  @override
  String engineeringDaemonStatusFailed(String error) {
    return 'Could not check status: $error';
  }

  @override
  String get engineeringExecutableTitle => 'aura_hostd executable';

  @override
  String get engineeringExecutableHelp =>
      'Installed builds use the aura_hostd that ships with this app. To use another one, point this at it (see aura_hostd/README.md); clear it to go back to the bundled one.';

  @override
  String get engineeringDaemonNotRunning => 'Not running';

  @override
  String get engineeringDaemonRunningNotPaired => 'Running, not paired';

  @override
  String get engineeringDaemonRunningOtherServer =>
      'Running, paired to a different server';

  @override
  String get engineeringDaemonRunningPaired => 'Running, paired';

  @override
  String get engineeringDaemonReconnecting => 'Running, reconnecting...';

  @override
  String get engineeringDaemonPairedUpdateNeeded =>
      'Running, paired -- update aura_hostd to see connection status';

  @override
  String engineeringDaemonBackend(String url) {
    return 'Backend: $url';
  }

  @override
  String engineeringDaemonOtherServerNote(String url) {
    return 'This app is signed in to $url. Pair again to move the daemon there.';
  }

  @override
  String get engineeringDaemonStart => 'Start';

  @override
  String get engineeringDaemonStop => 'Stop';

  @override
  String get engineeringPairAgain => 'Pair again';

  @override
  String get engineeringWorkspaceRoots => 'Workspace roots';

  @override
  String get engineeringNoRoots =>
      'No folders granted yet -- the agent can\'t read or write anything on this machine until you add one.';

  @override
  String get engineeringRootTrusted =>
      'Trusted -- commands run without per-call confirmation (still confined to this folder)';

  @override
  String get engineeringRootUntrusted =>
      'Untrusted -- only a read-only inspection allowlist runs';

  @override
  String get engineeringBrowse => 'Browse';

  @override
  String get engineeringDaemonOutput => 'Daemon output';

  @override
  String get engineeringNoLogsNote =>
      'Live output is only available for a daemon this app started -- pointing at one already running elsewhere still works for everything else on this page.';

  @override
  String get engineeringBackToFileList => 'Back to file list';

  @override
  String get engineeringFileTruncated =>
      'File is larger than this preview shows.';

  @override
  String engineeringReadDirFailed(String error) {
    return 'Could not read this directory: $error';
  }

  @override
  String get engineeringFolderEmpty => 'This folder is empty.';

  @override
  String get engineeringGithubBrowserFailed =>
      'Could not open the browser to connect GitHub.';

  @override
  String get engineeringGithubDisconnected => 'GitHub disconnected.';

  @override
  String get engineeringGithubConnectedUnknown =>
      'Connected as a GitHub account';

  @override
  String engineeringGithubConnectedAs(String login) {
    return 'Connected as $login';
  }

  @override
  String get engineeringGithubNotConnected => 'No GitHub account connected';

  @override
  String engineeringGithubConnectedAt(String date) {
    return 'Connected $date';
  }

  @override
  String get engineeringGithubConnectedBody =>
      'The engineer agent can read and write exactly the repositories you granted when you installed the GitHub App. Disconnecting only removes what AURA can use -- it does not revoke the app on github.com.';

  @override
  String get engineeringGithubDisconnectedBody =>
      'Connect your own GitHub account so the engineer agent (opening PRs, reading issues, committing files) acts on the repositories you choose, instead of relying on a shared token an operator configured.';

  @override
  String get engineeringGithubDisconnect => 'Disconnect GitHub';

  @override
  String get engineeringGithubConnect => 'Connect GitHub';

  @override
  String get engineeringDesktopDaemonTooltip => 'Desktop daemon';

  @override
  String get engineeringNoRunsTitle => 'No engineer runs yet';

  @override
  String get engineeringNoRunsMessage =>
      'Ask the Engineer agent to open a PR or run a command, and it will show up here.';

  @override
  String get engineeringToolOpenedDraftPr => 'Opened draft PR';

  @override
  String get engineeringToolCommittedFiles => 'Committed files';

  @override
  String get engineeringToolCreatedBranch => 'Created branch';

  @override
  String get engineeringToolWroteFile => 'Wrote file';

  @override
  String get engineeringToolEditedFile => 'Edited file';

  @override
  String get engineeringToolRanCommand => 'Ran command';

  @override
  String get engineeringToolCommitted => 'Committed';

  @override
  String get engineeringToolSwitchedBranch => 'Switched branch';

  @override
  String get engineeringToolDiffedSandbox => 'Diffed sandbox';

  @override
  String get engineeringRunStatusPending => 'pending';

  @override
  String get engineeringRunStatusSucceeded => 'succeeded';

  @override
  String get engineeringRunStatusFailed => 'failed';

  @override
  String engineeringRunStatus(String status) {
    return 'Status: $status';
  }

  @override
  String engineeringRunBranch(String branch) {
    return 'Branch: $branch';
  }

  @override
  String engineeringRunCommit(String sha) {
    return 'Commit: $sha';
  }

  @override
  String get engineeringCopyLink => 'Copy link';

  @override
  String get engineeringLinkCopied => 'Link copied';

  @override
  String get engineeringRunOutput => 'Output';

  @override
  String get automationsTitle => 'AURA Automations';

  @override
  String get automationsSubtitle =>
      'Extract tasks from emails and issues, generate workspace structures';

  @override
  String get automationsTabGmail => 'Gmail Inbox Analyzer';

  @override
  String get automationsTabGithub => 'GitHub Issue Importer';

  @override
  String get automationsTabDrive => 'Drive Workspace Builder';

  @override
  String get automationsGmailUnavailableTitle =>
      'Live Gmail Integration Unavailable';

  @override
  String get automationsGmailUnavailableBody =>
      'You are signed in via a mock developer profile. Real unread emails can only be fetched when logged in with a real Google Account.';

  @override
  String get automationsGmailRecentUnread => 'Recent Unread Emails';

  @override
  String get automationsGmailFetchUnread => 'Fetch Unread';

  @override
  String get automationsGmailFetching => 'Fetching...';

  @override
  String get automationsGmailEmpty => 'No unread emails loaded. Click Fetch.';

  @override
  String automationsGmailFrom(String sender) {
    return 'From: $sender';
  }

  @override
  String get automationsGmailAnalyze => 'AI Analyze';

  @override
  String automationsGmailCategory(String category) {
    return 'Category: $category';
  }

  @override
  String get automationsGmailUrgent => 'URGENT';

  @override
  String get automationsGmailLowUrgency => 'Low Urgency';

  @override
  String get automationsGmailAiSummary => 'AI Summary';

  @override
  String get automationsGmailActionItems => 'Action Items Extracted';

  @override
  String automationsGmailSuggestedDeadline(String date) {
    return 'Suggested Deadline: $date';
  }

  @override
  String get automationsGmailImportTask => 'Import as AURA Task';

  @override
  String automationsGmailImportedTask(String title) {
    return 'Imported task: $title';
  }

  @override
  String get automationsGmailSuggestedEvent => 'Suggested Calendar Event';

  @override
  String get automationsGmailScheduleEvent => 'Schedule Event';

  @override
  String automationsGmailScheduledEvent(String title) {
    return 'Scheduled event: $title';
  }

  @override
  String automationsGmailFetchError(String error) {
    return 'Gmail Fetch Error: $error';
  }

  @override
  String automationsGmailAnalysisFailed(String error) {
    return 'Analysis failed: $error';
  }

  @override
  String get automationsGmailNoSubject => 'No Subject';

  @override
  String get automationsGmailUnknownSender => 'Unknown Sender';

  @override
  String get automationsGithubSetup => 'GitHub Integration Setup';

  @override
  String get automationsGithubPatLabel => 'GitHub PAT (Personal Access Token)';

  @override
  String get automationsGithubRepoLabel => 'Repository Path';

  @override
  String get automationsGithubLoadIssues => 'Load Repo Issues';

  @override
  String get automationsGithubLoading => 'Loading GitHub Board...';

  @override
  String get automationsGithubEmpty => 'No open issues loaded.';

  @override
  String get automationsGithubExtractSubtasks => 'Extract Subtasks';

  @override
  String automationsGithubComplexity(String complexity) {
    return 'Complexity: $complexity';
  }

  @override
  String automationsGithubPriority(String priority) {
    return 'Priority: $priority';
  }

  @override
  String get automationsGithubIssueSummary => 'Issue Summary';

  @override
  String get automationsGithubCodingTasks => 'Extracted Coding Tasks';

  @override
  String get automationsGithubImported => 'Imported coding task!';

  @override
  String get automationsGithubMissingInput =>
      'Please provide both GitHub PAT and Repository Path.';

  @override
  String get automationsGithubRepoFormat =>
      'Repository Path must be in \'owner/repo\' format (e.g., owner/AURA-AI).';

  @override
  String automationsGithubFetchFailed(String error) {
    return 'GitHub fetch failed: $error';
  }

  @override
  String automationsGithubAnalysisFailed(String error) {
    return 'Issue analysis failed: $error';
  }

  @override
  String get automationsDriveUnavailableTitle =>
      'Google Drive Workspace Creation Unavailable';

  @override
  String get automationsDriveUnavailableBody =>
      'You are signed in via a mock developer profile. Drive workspace directories can only be generated when logged in with a real Google Account.';

  @override
  String get automationsDriveGeneratorTitle =>
      'Drive Project Workspace Generator';

  @override
  String get automationsDriveGeneratorBody =>
      'Enter project details. AURA will generate a Google Drive folder and populate it with a structured Markdown project outline document.';

  @override
  String get automationsDriveProjectName => 'Project Name';

  @override
  String get automationsDriveProjectNameHint => 'e.g. Freelance Mobile App';

  @override
  String get automationsDriveProjectDescription =>
      'Project Description / Outline';

  @override
  String get automationsDriveProjectDescriptionHint =>
      'Describe details to generate template plan';

  @override
  String get automationsDriveGenerate => 'Generate Drive Workspace';

  @override
  String get automationsDriveConsole => 'Automation Activity Console';

  @override
  String get automationsDriveInitializing =>
      'Initializing project workspace folders...';

  @override
  String automationsDriveFailed(String error) {
    return 'Workspace creation failed: $error';
  }

  @override
  String get automationsDriveRequestingPermissions =>
      'Requesting Drive storage permissions...';

  @override
  String get automationsDriveConnected =>
      'Established connection with Google Drive.';

  @override
  String automationsDriveCreatedFolder(String name) {
    return 'Created Folder: $name';
  }

  @override
  String automationsDriveCreatedDocument(String name) {
    return 'Created document: $name';
  }

  @override
  String get automationsDriveDone => 'Drive Workspace generated successfully!';

  @override
  String get financeTitle => 'Finance Ledger';

  @override
  String get financeSubtitle =>
      'Track incomes, expenses, and analyze financial distributions';

  @override
  String get financeBalance => 'Balance';

  @override
  String get financeIncome => 'Income';

  @override
  String get financeExpenses => 'Expenses';

  @override
  String get financeExpensesByCategory => 'Expenses by Category';

  @override
  String get financeNoExpenses => 'No expenses logged';

  @override
  String get financeIncomeVsExpense => 'Income vs Expense';

  @override
  String get financeExpense => 'Expense';

  @override
  String get financeLogTransaction => 'Log Transaction';

  @override
  String financeAmountLabel(String currency) {
    return 'Amount ($currency)';
  }

  @override
  String get financeCategory => 'Category';

  @override
  String get financeCategoryFood => 'Food';

  @override
  String get financeCategoryStudies => 'Studies';

  @override
  String get financeCategoryFreelance => 'Freelance';

  @override
  String get financeCategoryHousing => 'Housing';

  @override
  String get financeCategoryEntertainment => 'Entertainment';

  @override
  String get financeCategoryUtilities => 'Utilities';

  @override
  String get financeCategoryTransport => 'Transport';

  @override
  String get financeCategoryOther => 'Other';

  @override
  String get financeDescriptionLabel => 'Description (Optional)';

  @override
  String get financeDescriptionHint => 'e.g. Weekly grocery shopping';

  @override
  String get financeDate => 'Date';

  @override
  String get financeLogged => 'Transaction logged successfully!';

  @override
  String get financeSubmit => 'Submit Ledger Entry';

  @override
  String get financeInvalidAmount => 'Please enter a valid positive amount.';

  @override
  String get financeChooseCategory => 'Please choose a category.';

  @override
  String get financeRecentActivities => 'Recent Activities';

  @override
  String get financeNoActivities => 'No activities recorded yet';

  @override
  String financeDeletedEntry(String entry) {
    return 'Deleted entry: $entry';
  }

  @override
  String get ideClosePanel => 'Close panel';

  @override
  String get ideActivityExplorer => 'Explorer';

  @override
  String get ideActivityExplorerTooltip => 'Explorer (Ctrl+B)';

  @override
  String get ideActivityGoToFile => 'Go to File';

  @override
  String get ideActivityGoToFileTooltip => 'Go to File (Ctrl+P)';

  @override
  String get ideActivitySourceControl => 'Source Control';

  @override
  String get ideActivityAgents => 'AURA Agents';

  @override
  String get ideActivityAgentsTooltip => 'AURA Agents (Ctrl+J)';

  @override
  String get ideActivityControlCenterTooltip => 'AURA Control Center';

  @override
  String get ideActivityPanel => 'Panel';

  @override
  String get ideActivityPanelTooltip =>
      'Terminal, Output & Problems (Ctrl+Shift+J)';

  @override
  String ideActivityPanelProblems(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count problems',
      one: '1 problem',
    );
    return 'Panel, $_temp0';
  }

  @override
  String get ideBottomTerminal => 'Terminal';

  @override
  String get ideBottomOutput => 'Output';

  @override
  String ideBottomProblemsCount(int count) {
    return 'Problems ($count)';
  }

  @override
  String get ideBottomProblems => 'Problems';

  @override
  String get ideBottomDebugConsole => 'Debug Console';

  @override
  String get ideCmdNoWorkspace => 'No workspace is open';

  @override
  String get ideCmdNoFileOpen => 'No file is open';

  @override
  String get ideCmdGroupFile => 'File';

  @override
  String get ideCmdGroupView => 'View';

  @override
  String get ideCmdGoToFile => 'Go to File...';

  @override
  String get ideCmdSaveFile => 'Save File';

  @override
  String get ideCmdNoUnsavedChanges => 'No unsaved changes';

  @override
  String get ideCmdCloseEditor => 'Close Editor';

  @override
  String get ideCmdShowExplorer => 'Show Explorer';

  @override
  String get ideCmdShowSourceControl => 'Show Source Control';

  @override
  String get ideCmdShowControlCenter => 'Show AURA Control Center';

  @override
  String get ideCmdHideSidebar => 'Hide Side Bar';

  @override
  String get ideCmdShowSidebar => 'Show Side Bar';

  @override
  String get ideCmdHidePanel => 'Hide Panel';

  @override
  String get ideCmdShowPanel => 'Show Panel';

  @override
  String get ideCmdFocusTerminal => 'Focus Terminal';

  @override
  String get ideCmdHideAgent => 'Hide AURA Agent';

  @override
  String get ideCmdShowAgent => 'Show AURA Agent';

  @override
  String get ideCmdInlineCompletion => 'Request Inline Completion';

  @override
  String get ideCmdRefreshSourceControl => 'Refresh Source Control';

  @override
  String get ideCmdLeaveIde => 'Leave IDE (Open Today)';

  @override
  String get ideStatusNoBranch => 'No branch';

  @override
  String get ideStatusHostNotRunning => 'Host Not Running';

  @override
  String get ideStatusHostNotPaired => 'Host Not Paired';

  @override
  String get ideStatusHostOtherServer =>
      'Host paired to a different server -- re-pair';

  @override
  String get ideStatusHostConnected => 'Host Connected';

  @override
  String get ideStatusReconnecting => 'Reconnecting to server...';

  @override
  String get ideStatusHostUnknown => 'Host Status Unknown (update aura_hostd)';

  @override
  String ideStatusPendingApprovals(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count pending approvals',
      one: '1 pending approval',
    );
    return '$_temp0';
  }

  @override
  String ideStatusRolledBackTooltip(String version) {
    return 'This version was rolled back to $version';
  }

  @override
  String ideStatusUpdateAvailableTooltip(String version) {
    return 'AURA $version is available';
  }

  @override
  String ideStatusRollBackTo(String version) {
    return 'Roll back to $version';
  }

  @override
  String ideStatusUpdateTo(String version) {
    return 'Update to $version';
  }

  @override
  String ideStatusLineColumn(int line, int column) {
    return 'Ln $line, Col $column';
  }

  @override
  String get ideStatusLineEndingMixed => 'Mixed';

  @override
  String ideUpdateRollbackTitle(String version) {
    return 'Roll back to AURA $version?';
  }

  @override
  String ideUpdateTitle(String version) {
    return 'Update to AURA $version?';
  }

  @override
  String ideUpdateWindowsRollbackBody(String version) {
    return 'This version has been rolled back. AURA will close, install $version and reopen. Unsaved changes are kept and restored.';
  }

  @override
  String ideUpdateWindowsBody(String version) {
    return 'AURA will close, install $version and reopen. Unsaved changes are kept and restored.';
  }

  @override
  String ideUpdateLinuxBody(String version) {
    return 'Download AURA $version for Linux in your browser.';
  }

  @override
  String get ideNotNow => 'Not now';

  @override
  String get ideUpdateInstallRestart => 'Install and restart';

  @override
  String get ideUpdateDownload => 'Download';

  @override
  String get ideUpdateDownloading => 'Downloading the update...';

  @override
  String get ideUpdateChecksumFailed =>
      'The download did not match the release checksum and was deleted. Nothing was installed.';

  @override
  String ideUpdateFailed(String error) {
    return 'Could not update: $error';
  }

  @override
  String get idePendingApprovalsTitle => 'Pending Approvals';

  @override
  String ideApprovalFilesInRoot(int count, String root) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count files in $root',
      one: '1 file in $root',
    );
    return '$_temp0';
  }

  @override
  String ideApprovalFiles(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count files',
      one: '1 file',
    );
    return '$_temp0';
  }

  @override
  String get ideApprove => 'Approve';

  @override
  String get explorerGrantTitle => 'Grant workspace to hostd?';

  @override
  String explorerGrantBody(String root) {
    return 'This allows the AURA agent to read and write files in \"$root\".';
  }

  @override
  String get explorerGrant => 'Grant';

  @override
  String get explorerAccessGranted => 'Workspace access granted';

  @override
  String explorerGrantFailed(String error) {
    return 'Could not grant root: $error';
  }

  @override
  String get explorerHostOffline => 'Host offline';

  @override
  String get explorerNoWorkspaceTitle => 'No workspace open';

  @override
  String get explorerNoWorkspaceMessage =>
      'Open a folder from the welcome page to browse its files here.';

  @override
  String get explorerAccessRequiredTitle => 'Workspace access required';

  @override
  String get explorerLoadFailedTitle => 'Failed to load directory';

  @override
  String get explorerAccessRequiredBody =>
      'This folder is outside every root granted to the host daemon. Grant access to browse it here and let the AURA agent work in it.';

  @override
  String get explorerGrantAccess => 'Grant Access';

  @override
  String get explorerOutsideGrantedRoots =>
      'This folder is outside every root granted to the host daemon.';

  @override
  String get explorerHostUnreachable => 'The host daemon is unreachable.';

  @override
  String get explorerDirectoryNotFound => 'Directory not found';

  @override
  String get explorerDeleteFolderTitle => 'Delete folder?';

  @override
  String get explorerDeleteFileTitle => 'Delete file?';

  @override
  String explorerDeleteFolderBody(String name) {
    return '\"$name\" and everything inside it will be permanently deleted.';
  }

  @override
  String explorerDeleteFileBody(String name) {
    return '\"$name\" will be permanently deleted.';
  }

  @override
  String get explorerRenameFolderTitle => 'Rename folder';

  @override
  String get explorerRenameFileTitle => 'Rename file';

  @override
  String get explorerNewName => 'New name';

  @override
  String get explorerRename => 'Rename';

  @override
  String explorerRenameFailed(String error) {
    return 'Could not rename: $error';
  }

  @override
  String explorerDeleteFailed(String error) {
    return 'Could not delete: $error';
  }

  @override
  String get explorerNewFileTitle => 'New file';

  @override
  String get explorerFileName => 'File name';

  @override
  String get explorerCreate => 'Create';

  @override
  String explorerCreateFileFailed(String error) {
    return 'Could not create file: $error';
  }

  @override
  String get explorerNewFolderTitle => 'New folder';

  @override
  String get explorerFolderName => 'Folder name';

  @override
  String explorerCreateFolderFailed(String error) {
    return 'Could not create folder: $error';
  }

  @override
  String get explorerMenuOpen => 'Open';

  @override
  String get explorerMenuCopyPath => 'Copy path';

  @override
  String get explorerMenuCopyRelativePath => 'Copy relative path';

  @override
  String get explorerMenuReveal => 'Reveal in filesystem';

  @override
  String get explorerMenuOpenInTerminal => 'Open in Terminal';

  @override
  String get explorerMenuCollapse => 'Collapse';

  @override
  String get explorerMenuExpand => 'Expand';

  @override
  String get explorerMenuNewFile => 'New File';

  @override
  String get explorerMenuNewFolder => 'New Folder';

  @override
  String get explorerAccessRequired => 'Access required';

  @override
  String get explorerLoadFailed => 'Failed to load';

  @override
  String get explorerEmpty => 'Empty';

  @override
  String get explorerPathCopied => 'Path copied';

  @override
  String get explorerRevealWindowsOnly =>
      'Reveal in filesystem is only supported on Windows for now';

  @override
  String explorerRevealFailed(String error) {
    return 'Could not open filesystem: $error';
  }

  @override
  String get gitDiffTruncated =>
      'Diff truncated -- view the full diff in a terminal.';

  @override
  String get gitMergeDialogTitle => 'Merge branch into current';

  @override
  String gitRemoteBranch(String name) {
    return '$name (remote)';
  }

  @override
  String get gitCreateBranchTitle => 'Create branch';

  @override
  String get gitBranchNameHint => 'Branch name';

  @override
  String get gitNoWorkspace => 'Open a workspace to use Source Control.';

  @override
  String get gitGrantRequired =>
      'Grant this workspace to the host daemon to use Source Control.';

  @override
  String get gitFetchTooltip => 'Fetch from all remotes';

  @override
  String get gitPullTooltip => 'Pull (fast-forward only)';

  @override
  String get gitPushTooltip => 'Push current branch to its upstream';

  @override
  String get gitMerge => 'Merge';

  @override
  String get gitBranch => 'Branch';

  @override
  String get gitCommitMessageHint => 'Commit message';

  @override
  String get gitWorking => 'Working...';

  @override
  String get gitCommit => 'Commit';

  @override
  String get gitHistory => 'History';

  @override
  String get gitNoCommits => 'No commits loaded.';

  @override
  String get gitStagedChanges => 'Staged Changes';

  @override
  String get gitUnstageAll => 'Unstage All';

  @override
  String get gitUnstage => 'Unstage';

  @override
  String get gitChanges => 'Changes';

  @override
  String get gitStageAll => 'Stage All';

  @override
  String get gitStage => 'Stage';

  @override
  String get gitNoChanges => 'No changes.';

  @override
  String get gitDetachedHead => '(detached HEAD)';

  @override
  String gitConflictCount(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count files have merge conflicts',
      one: '1 file has merge conflicts',
    );
    return '$_temp0';
  }

  @override
  String get gitAbortMerge => 'Abort Merge';

  @override
  String get gitMarkResolved => 'Mark Resolved';

  @override
  String get gitNothingStaged => 'Nothing is staged to commit.';

  @override
  String get gitCheckoutOverwrite =>
      'Switching branches would overwrite local changes.';

  @override
  String gitCheckoutOverwriteIn(String files) {
    return 'Switching branches would overwrite local changes in: $files';
  }

  @override
  String get gitNoUpstream => 'The current branch has no upstream set.';

  @override
  String get gitDiverged =>
      'The branch has diverged -- pull (or push) cannot fast-forward.';

  @override
  String get gitLocalChangesOverwritten =>
      'Local changes would be overwritten.';

  @override
  String gitLocalChangesOverwrittenIn(String files) {
    return 'Local changes would be overwritten in: $files';
  }

  @override
  String get gitRemoteSyncFailed => 'Remote sync failed.';

  @override
  String get workspaceWelcomeSubtitle =>
      'Open a folder to start coding with AURA';

  @override
  String get workspaceOpenFolder => 'Open Folder';

  @override
  String get workspaceRecent => 'Recent';

  @override
  String workspaceGrantBody(String path) {
    return 'This allows the AURA agent to read/write files in \"$path\".';
  }

  @override
  String get workspaceNotNow => 'Not Now';

  @override
  String get workspaceNoRecent => 'No recent workspaces';

  @override
  String get workspaceNoRecentHint => 'Open a folder to get started';

  @override
  String get workspaceRemoveFromRecent => 'Remove from recent';

  @override
  String get workspaceManagerTitle => 'Workspaces';

  @override
  String get workspaceRecentWorkspaces => 'Recent Workspaces';

  @override
  String get workspaceActive => 'Active';

  @override
  String workspaceLastOpened(String date) {
    return 'Last opened: $date';
  }

  @override
  String get workspaceOpen => 'Open';

  @override
  String get workspaceRemoveTitle => 'Remove workspace?';

  @override
  String workspaceRemoveBody(String name) {
    return 'This will remove \"$name\" from your recent workspaces.';
  }

  @override
  String get workspaceRemove => 'Remove';

  @override
  String get workspaceEmptyTitle => 'No workspaces yet';

  @override
  String get workspaceEmptyMessage =>
      'Open a folder to create your first workspace';
}
