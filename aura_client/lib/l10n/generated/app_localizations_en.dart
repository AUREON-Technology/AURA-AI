// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class AppLocalizationsEn extends AppLocalizations {
  AppLocalizationsEn([String locale = 'en']) : super(locale);

  @override
  String get chatRetryOnlyLatestTurn =>
      'Later messages followed this one, so it can\'t be retried here. Send it again if you still need an answer.';

  @override
  String get chatImageCapabilityCheckFailed =>
      'Couldn\'t check whether images can be attached. Tap to try again.';

  @override
  String get memorySaveQueued =>
      'Saved on this device. AURA will remember it once it can reach the server.';

  @override
  String memorySaveRefused(String fact) {
    return 'The server didn\'t accept this memory: $fact';
  }

  @override
  String shareTruncatedOlder(int count, int omitted) {
    return 'Only the latest $count messages fit in a shared chat, so the $omitted older ones won\'t be included.';
  }

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
  String get onboardingPrivacyTitle => 'Where your requests are processed';

  @override
  String get onboardingPrivacyBody =>
      'Nothing is processed on this phone. Your documents, memories and files are only read by AI models AURA runs itself. The exception is the Engineer: once you agree in the app, the messages you send it, the files it opens and the output of the commands it runs go to Claude, an AI model run by Anthropic. Other messages may be answered by an approved hosted AI provider, if the server’s operator allows it.';

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
      'Requests are handled by AURA’s servers, never on this device. Your documents, memories, files and tool results are only read by AI models AURA runs itself. The exception is the Engineer: once you agree in the app, what it reads and runs goes to Claude, an AI model run by Anthropic. If the server’s operator turns it on, your other messages may be answered by an approved hosted AI provider.';

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
  String get loginSessionEndedPlanRequired =>
      'You\'ve been signed out: your plan no longer includes the Engineer on your computer. Upgrade to Pro or Team, then sign in again — everything on this device is still here.';

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
  String hostdPairedToOtherServer(String appBackendUrl) {
    return 'This app is signed in to $appBackendUrl. Pair again to move the daemon there.';
  }

  @override
  String get liveVoiceEnd => 'End live voice';

  @override
  String get voiceLanguageLabel => 'Voice language';

  @override
  String get voiceLiveArabicUnavailable =>
      'Arabic live voice isn\'t available on this server yet, so this session continues in English.';

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
      'Confirm the action below to continue.';

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
  String get hostRequiredNotRunning =>
      'The host daemon isn\'t running. Start it so AURA can reach this workspace\'s files.';

  @override
  String get hostRequiredNotPaired =>
      'The host daemon is running but isn\'t paired with your account. Pair it to continue.';

  @override
  String get agentHostNotRunning =>
      'Host daemon isn\'t running. Engineer can\'t read or change files until it starts.';

  @override
  String get agentHostNotPaired =>
      'Host daemon isn\'t paired. Engineer can\'t read or change files until you pair it.';

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
      'No Engineer tasks to review yet. Each Engineer chat works in its own copy of the folder, on a branch named aura/task-…, and shows up here for you to review and merge.';

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
  String get editorDiskChangedTitle => 'File changed on disk';

  @override
  String editorDiskChangedBody(String title) {
    return '\"$title\" was changed outside the editor since you opened it. Saving now would overwrite those changes.';
  }

  @override
  String get editorOverwrite => 'Overwrite';

  @override
  String get editorReloadFromDisk => 'Reload from disk';

  @override
  String editorSaveFailed(String title, String error) {
    return 'Could not save $title: $error';
  }

  @override
  String get editorNotEditable =>
      'This file can\'t be edited because it could not be opened.';

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
      'The Engineer isn\'t part of this workspace. It comes with the Pro and Team plans, or an operator can grant it to your role. Until then, messages from here would be answered by the general assistant instead.';

  @override
  String get agentLockedElsewhereTitle =>
      'The Engineer is in another workspace';

  @override
  String agentLockedElsewhereMessage(String workspace) {
    return 'This workspace\'s plan doesn\'t include the Engineer, but $workspace does. Switch to it to use the Engineer here.';
  }

  @override
  String agentLockedSwitchTo(String workspace) {
    return 'Switch to $workspace';
  }

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
      'Trusted -- the agent\'s commands run without asking, as you, and can reach anything your account can. Paths outside this folder and any git push ask first.';

  @override
  String get engineeringRootUntrusted =>
      'Untrusted -- read-only commands run; anything else asks you first';

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
  String get explorerRevealUnsupported =>
      'Reveal in filesystem is not supported on this platform';

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
  String get gitPushTooltip => 'Push (follows your git configuration)';

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
  String gitBranchExists(String branch) {
    return 'A branch named \"$branch\" already exists. Choose another name, or switch to it from the branch list.';
  }

  @override
  String get gitCheckoutOverwrite =>
      'Switching branches would overwrite local changes.';

  @override
  String gitCheckoutOverwriteIn(String files) {
    return 'Switching branches would overwrite local changes in: $files';
  }

  @override
  String get gitNoUpstream =>
      'The current branch has no upstream set. Use Publish branch to push it.';

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
  String get gitAuthFailed =>
      'Signing in to the remote failed. Sign in to this remote in your terminal or credential manager, then try again.';

  @override
  String get gitPublishBranch => 'Publish branch';

  @override
  String get gitPublishTooltip =>
      'Push this branch to a remote and set it as the upstream';

  @override
  String get gitPublishDialogTitle => 'Publish branch';

  @override
  String gitPublishConfirm(String branch, String remote) {
    return 'Publish \"$branch\" to \"$remote\" and use it as this branch\'s upstream?';
  }

  @override
  String gitPublishChooseRemote(String branch) {
    return 'Publish \"$branch\" to which remote? It will become this branch\'s upstream.';
  }

  @override
  String get gitPublishAction => 'Publish';

  @override
  String get gitNoRemote =>
      'This repository has no remote to publish to. Add one first (git remote add).';

  @override
  String get gitPublishUnknownRemote => 'That remote is no longer configured.';

  @override
  String get gitPublishDetachedHead =>
      'Check out a branch first; a detached HEAD cannot be published.';

  @override
  String get gitPublishNoCommits =>
      'Make a first commit before publishing this branch.';

  @override
  String get gitPublishDiverged =>
      'The remote already has a different branch with this name.';

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

  @override
  String relativeTodayAt(String time) {
    return 'Today $time';
  }

  @override
  String get relativeYesterday => 'Yesterday';

  @override
  String relativeDaysAgo(int days) {
    String _temp0 = intl.Intl.pluralLogic(
      days,
      locale: localeName,
      other: '$days days ago',
      one: '1 day ago',
    );
    return '$_temp0';
  }

  @override
  String get coreEngineerWorkspacesUnavailableTitle => 'Workspaces unavailable';

  @override
  String get coreEngineerWorkspacesUnavailableMessage =>
      'Could not reach the server. Your work is not lost -- try again shortly.';

  @override
  String get coreEngineerWorkspacesDisabledTitle =>
      'Workspaces are not enabled';

  @override
  String get coreEngineerWorkspacesDisabledMessage =>
      'This server does not run the engineer agent’s sandboxes.';

  @override
  String get coreEngineerWorkspacesEmptyTitle => 'No workspaces yet';

  @override
  String coreEngineerWorkspacesEmptyMessage(int hours) {
    return 'Ask the engineer agent to clone a repository and one will appear here. Workspaces are removed after $hours hours without activity.';
  }

  @override
  String get coreEngineerWorkspacesHeader => 'Engineer workspaces';

  @override
  String coreEngineerWorkspaceSemanticLabel(String repo) {
    return 'Workspace for $repo';
  }

  @override
  String coreEngineerWorkspaceBranch(String branch) {
    return 'Branch $branch';
  }

  @override
  String get coreEngineerWorkspaceDeleteTitle => 'Delete this workspace?';

  @override
  String get coreEngineerWorkspaceKeep => 'Keep';

  @override
  String coreEngineerWorkspaceDeleteBody(String repo) {
    return 'The checkout of $repo and any uncommitted changes in it will be removed. Anything already pushed is unaffected.';
  }

  @override
  String get coreEngineerWorkspaceDeleted => 'Workspace deleted.';

  @override
  String get coreEngineerWorkspaceDeleteFailed =>
      'Could not delete that workspace.';

  @override
  String get coreEngineerWorkspaceExpiryOverdue => 'Due to be removed';

  @override
  String get coreEngineerWorkspaceExpiryUnderHour =>
      'Removed in under an hour unless used';

  @override
  String coreEngineerWorkspaceExpiryHours(int hours) {
    String _temp0 = intl.Intl.pluralLogic(
      hours,
      locale: localeName,
      other: 'Removed in ${hours}h unless used',
    );
    return '$_temp0';
  }

  @override
  String coreEngineerWorkspaceExpiryDays(int days) {
    String _temp0 = intl.Intl.pluralLogic(
      days,
      locale: localeName,
      other: 'Removed in ${days}d unless used',
    );
    return '$_temp0';
  }

  @override
  String get coreEngineerWorkspaceRunning => 'Running';

  @override
  String get coreEngineerWorkspaceIdle => 'Idle';

  @override
  String get voiceClientNothingToRead => 'Nothing to read in this reply.';

  @override
  String get voiceClientSpeechFailed =>
      'Could not generate speech for this reply.';

  @override
  String get voiceClientUnreachable =>
      'Could not reach AURA to generate speech.';

  @override
  String get voiceClientSignInAgain => 'Sign in again to continue.';

  @override
  String get voiceClientSpeakNotAvailable =>
      'Speech playback is not available on this server.';

  @override
  String get voiceClientReplyTooLong => 'This reply is too long to read aloud.';

  @override
  String get voiceClientRateLimited =>
      'Too many speech requests. Try again shortly.';

  @override
  String get voiceClientNoSpeechEngine =>
      'This server has no speech engine configured.';

  @override
  String get voiceSocketSessionExpired =>
      'Live voice ended because your sign-in needs renewing. Start it again.';

  @override
  String get voiceSocketDisconnected =>
      'Live voice was disconnected. Start it again.';

  @override
  String get coreBackendEnvironmentProduction => 'Production';

  @override
  String get coreBackendEnvironmentDevelopment => 'Development';

  @override
  String get coreAskCopilotTooltip => 'Ask Copilot';

  @override
  String get coreDesktopSignInBrowserFailed =>
      'Could not open the system browser.';

  @override
  String get coreDesktopSignInTimedOut =>
      'Timed out waiting for the browser sign-in to complete.';

  @override
  String coreDesktopSignInPageCancelled(String error) {
    return 'Sign-in was cancelled ($error).';
  }

  @override
  String coreDesktopSignInGoogleError(String error) {
    return 'Google returned an error: $error';
  }

  @override
  String get coreDesktopSignInPageInvalidResponse =>
      'Sign-in failed: invalid response.';

  @override
  String get coreDesktopSignInInvalidRedirect =>
      'Missing authorization code or a state mismatch (possible CSRF).';

  @override
  String get coreDesktopSignInPageSuccess =>
      'Signed in -- you can close this tab.';

  @override
  String get coreDesktopSignInServerUnreachable =>
      'Could not reach the AURA server to finish signing in. Check your connection and try again.';

  @override
  String get coreDesktopSignInServerInsecure =>
      'Could not make a secure connection to the AURA server to finish signing in.';

  @override
  String get coreDesktopSignInServerBadAnswer =>
      'The AURA server did not answer properly while finishing sign-in. Try again.';

  @override
  String get coreDesktopSignInServerTimedOut =>
      'The AURA server took too long to finish signing in. Try again.';

  @override
  String coreDesktopSignInServerFailed(int status) {
    return 'The AURA server could not finish signing in (HTTP $status).';
  }

  @override
  String get coreDesktopSignInNoIdentityToken =>
      'The AURA server returned no identity token.';

  @override
  String get coreDesktopSignInPageSignedIn => 'Signed in';

  @override
  String get coreDesktopSignInPageFailed => 'Sign-in failed';

  @override
  String get failureFeatureUnavailableTitle =>
      'This needs a feature that isn\'t available yet';

  @override
  String get failureFeatureUnavailableMessage =>
      'AURA can answer plain questions right now. Answers that use your memory, documents, tools or images are coming soon.';

  @override
  String get failureSignInRefusedTitle => 'AURA did not accept this sign-in';

  @override
  String get failureSignInRefusedMessage =>
      'The server refused this account, so you have not been signed in. Try another account, or ask whoever runs this AURA server to check this one.';

  @override
  String get failureDesktopPlanRequiredTitle =>
      'The desktop app needs a Pro or Team plan';

  @override
  String get failureDesktopPlanRequiredMessage =>
      'The Engineer on your computer is part of the Pro and Team plans. Upgrade your plan, then sign in again.';

  @override
  String get failureSignInIncompleteTitle => 'Couldn\'t finish signing in';

  @override
  String get failureUnclassifiedMessage =>
      'AURA hit an error it could not classify. Your work is unaffected.';

  @override
  String get failureUnreachableTitle => 'Couldn\'t reach AURA';

  @override
  String get failureUnreachableMessage =>
      'Nothing answered — this may be your connection or the server. Anything you have already saved is safe on this device.';

  @override
  String get failureInferenceUnavailableTitle =>
      'AURA reached the server, but the model is not answering';

  @override
  String get failureInferenceUnavailableMessage =>
      'The Control Plane is up; inference is not. Your workspace and conversations are untouched.';

  @override
  String get failureBusyTitle => 'AURA is busy';

  @override
  String get failureBusyMessage =>
      'Too much is running at once. Waiting a moment usually clears it.';

  @override
  String get failureRateLimitedTitle => 'Too many requests';

  @override
  String get failureRateLimitedMessage =>
      'You have hit the request limit. Try again shortly.';

  @override
  String get failureTurnInFlightTitle =>
      'AURA is still working on your last request';

  @override
  String get failureTurnInFlightMessage =>
      'Only one turn runs at a time. Wait for it to finish, or stop it first.';

  @override
  String get failureAuthRequiredTitle => 'Sign in again';

  @override
  String get failureAuthRequiredMessage =>
      'Your session expired. Signing in again picks up exactly where you left off.';

  @override
  String get failureForbiddenTitle => 'Not allowed';

  @override
  String get failureForbiddenMessage =>
      'Your account does not have permission for this. Retrying will not change that.';

  @override
  String get failureBudgetTitle => 'Budget reached';

  @override
  String get failureBudgetMessage =>
      'This request would go over the configured spend limit.';

  @override
  String get failureBlockedTitle => 'AURA declined this request';

  @override
  String get failureBlockedMessage =>
      'It was refused on safety grounds. Sending the same thing again will be refused the same way — rephrasing is what changes the outcome.';

  @override
  String get failureIndexingTitle => 'Still indexing';

  @override
  String get failureIndexingMessage =>
      'AURA is building the search index for this content. It will work once that finishes.';

  @override
  String get failureDegradedTitle => 'AURA is degraded';

  @override
  String get failureDegradedMessage =>
      'Part of the system did not respond in time. Your data is unaffected.';

  @override
  String get failureServerErrorTitle => 'AURA had a problem';

  @override
  String get failureServerErrorMessage =>
      'The server failed to complete this. Your data is unaffected.';

  @override
  String get failureUnknownMessage =>
      'AURA could not complete this request. Your work is unaffected.';

  @override
  String failureRateLimitedWaitSeconds(int seconds) {
    String _temp0 = intl.Intl.pluralLogic(
      seconds,
      locale: localeName,
      other: '$seconds seconds',
      one: '1 second',
    );
    return 'You have hit the request limit. Try again in $_temp0.';
  }

  @override
  String failureRateLimitedWaitMinutes(int minutes) {
    String _temp0 = intl.Intl.pluralLogic(
      minutes,
      locale: localeName,
      other: '$minutes minutes',
      one: '1 minute',
    );
    return 'You have hit the request limit. Try again in about $_temp0.';
  }

  @override
  String get coreApiErrorUnreachable => 'Could not reach AURA.';

  @override
  String coreApiErrorUnreachableCause(String cause) {
    return 'Could not reach AURA: $cause';
  }

  @override
  String get coreApiErrorBlocked => 'AURA blocked this turn.';

  @override
  String get coreApiErrorSignInAgain => 'Sign in again to continue.';

  @override
  String get coreApiErrorForbidden =>
      'You do not have permission for this action.';

  @override
  String get coreApiErrorRateLimited => 'Too many requests. Try again shortly.';

  @override
  String get coreApiErrorStartingUp =>
      'AURA is starting up. Try again in a moment.';

  @override
  String coreApiErrorRequestFailed(int status) {
    return 'Request failed ($status).';
  }

  @override
  String get coreSyncSynced => 'Synced';

  @override
  String get coreSyncSyncing => 'Syncing';

  @override
  String get coreSyncOffline => 'Offline';

  @override
  String get coreSyncFailed => 'Sync failed';

  @override
  String coreSyncPendingRefused(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count changes not synced',
      one: '1 change not synced',
    );
    return '$_temp0';
  }

  @override
  String coreSyncStatusSemantic(String label) {
    return 'Sync status: $label';
  }

  @override
  String coreSyncParkedBanner(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other:
          '$count changes haven\'t reached the server. They\'re saved on this device.',
      one: '1 change hasn\'t reached the server. It\'s saved on this device.',
    );
    return '$_temp0';
  }

  @override
  String coreSyncParkedBannerSemantic(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other:
          '$count changes have not reached the server. They are saved on this device.',
      one: '1 change has not reached the server. It is saved on this device.',
    );
    return '$_temp0';
  }

  @override
  String get coreSyncReview => 'Review';

  @override
  String get coreHostdNotRunning => 'aura_hostd is not running.';

  @override
  String coreHostdUnreachable(String error) {
    return 'Could not reach aura_hostd: $error';
  }

  @override
  String get coreHostdOutOfDate =>
      'The local aura_hostd daemon does not recognize this request -- it may be out of date. Rebuild and restart aura_hostd, then try again.';

  @override
  String get coreHostdNotRunningStartIt =>
      'aura_hostd is not running. Start it and try again.';

  @override
  String get coreHostdNotOnWeb =>
      'The desktop Engineer daemon is not available on web.';

  @override
  String get coreHostdRejectedArguments =>
      'aura_hostd rejected its arguments (exit 64) -- check the configured executable';

  @override
  String coreHostdCrashedRepeatedly(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: 'aura_hostd crashed $count times in a row; see the log above',
    );
    return '$_temp0';
  }

  @override
  String coreHostdRestartFailed(String error) {
    return 'could not restart aura_hostd: $error';
  }

  @override
  String coreCommandItemUnavailable(String title, String reason) {
    return '$title, unavailable: $reason';
  }

  @override
  String chatActionCreateTask(String title) {
    return 'Create Task: $title';
  }

  @override
  String chatActionScheduleEvent(String title) {
    return 'Schedule Event: $title';
  }

  @override
  String chatActionLogExpense(String amount) {
    return 'Log Expense: \$$amount';
  }

  @override
  String chatActionLogIncome(String amount) {
    return 'Log Income: \$$amount';
  }

  @override
  String get chatActionLogHealth => 'Log Health';

  @override
  String chatActionUnknownNamed(String type) {
    return 'Unknown Action: $type';
  }

  @override
  String get chatActionUnknown => 'Unknown Action';

  @override
  String get chatSpeechGenerateFailed =>
      'Could not generate speech for this reply.';

  @override
  String get chatSpeechPlayFailed => 'Could not play the generated speech.';

  @override
  String get chatDefaultImageQuestion => 'What is in this image?';

  @override
  String get chatEngineUnreachable => 'AURA (unreachable)';

  @override
  String get chatEngineServer => 'AURA server';

  @override
  String chatCreateBranchOnRepo(String repo) {
    return 'Create branch on $repo';
  }

  @override
  String get chatCreateBranch => 'Create branch';

  @override
  String get chatConfirmActions => 'Confirm actions';

  @override
  String get chatConfirmAction => 'Confirm action';

  @override
  String get chatConfirmSelected => 'Confirm selected';

  @override
  String get chatConfirmSwitchBranch => 'Switch to branch';

  @override
  String get chatConfirmOpenDraftPr => 'Open draft pull request';

  @override
  String chatConfirmCommitFiles(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count files',
      one: '1 file',
    );
    return 'Commit $_temp0';
  }

  @override
  String chatConfirmCommitFilesToBranch(int count, String branch) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count files',
      one: '1 file',
    );
    return 'Commit $_temp0 to $branch';
  }

  @override
  String get chatConfirmWriteFile => 'Write file';

  @override
  String chatConfirmEditFile(String path) {
    return 'Edit $path';
  }

  @override
  String get chatConfirmEditFileUnnamed => 'Edit file';

  @override
  String get chatConfirmRunCommand => 'Run a command';

  @override
  String get chatConfirmCommitSandbox => 'Commit sandbox changes';

  @override
  String get chatTurnQuarantined =>
      'AURA treated the content it read as untrusted, so nothing with side effects can run without your confirmation.';

  @override
  String get chatTurnInterrupted => 'Interrupted — this answer is incomplete.';

  @override
  String get chatTurnRedacted => 'This answer was redacted by a safety check.';

  @override
  String chatTurnLowConfidence(int percent) {
    return 'Low confidence ($percent%).';
  }

  @override
  String get chatTurnLibraryEmpty =>
      'Searched your library — nothing relevant was found.';

  @override
  String get chatTurnReducedMode => 'AURA ran this turn in a reduced mode.';

  @override
  String get chatTurnUngrounded => 'Ungrounded — no sources attached.';

  @override
  String get chatTurnNoActionTaken =>
      'No action was taken — nothing was created, changed or sent.';

  @override
  String get chatTurnToolsUnavailable =>
      'AURA couldn\'t use its tools for this turn, so nothing was done.';

  @override
  String get chatTurnToolFailed => 'A tool AURA used failed during this turn.';

  @override
  String get chatTurnEmptyReply => 'The model returned no reply for this turn.';

  @override
  String get chatTurnToolOutputTrimmed =>
      'A tool\'s result was too long to use in full.';

  @override
  String chatMoreLines(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count more lines',
      one: '1 more line',
    );
    return '… $_temp0';
  }

  @override
  String chatImageReadFailed(String name) {
    return 'Could not read $name.';
  }

  @override
  String chatImageTooMany(int max) {
    return 'You can attach up to $max images to one message.';
  }

  @override
  String chatImageTooLarge(String name, String mb) {
    return '$name is larger than $mb MB.';
  }

  @override
  String chatImagesAttached(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: '$count images attached',
      one: '1 image attached',
    );
    return '$_temp0';
  }

  @override
  String chatActionCreateTaskDetail(
    String title,
    String priority,
    String category,
  ) {
    return 'Create Task: $title ($priority priority, $category)';
  }

  @override
  String chatActionScheduleEventAt(String title, String start) {
    return 'Schedule Event: $title at $start';
  }

  @override
  String chatActionLogExpenseFor(String amount, String category) {
    return 'Log Expense: \$$amount for $category';
  }

  @override
  String chatActionLogIncomeFor(String amount, String category) {
    return 'Log Income: \$$amount for $category';
  }

  @override
  String chatHealthWater(String ml) {
    return '${ml}ml water';
  }

  @override
  String chatHealthSleep(String minutes) {
    return '${minutes}m sleep';
  }

  @override
  String chatHealthExercise(String minutes) {
    return '${minutes}m exercise';
  }

  @override
  String chatActionLogHealthDetail(String metrics) {
    return 'Log Health: $metrics';
  }

  @override
  String get chatListSeparator => ', ';

  @override
  String get chatStepFallback => 'step';

  @override
  String chatStepBlocked(String detail) {
    return 'Blocked: $detail';
  }

  @override
  String get chatCapabilityEngineer => 'the Engineer capability';

  @override
  String get chatCapabilityAdditional => 'an additional capability';

  @override
  String chatPersonaRequires(String capability) {
    return 'Requires $capability -- not available on this account';
  }

  @override
  String chatPersonaSwitchToUse(String workspace) {
    return 'Included in $workspace -- tap to switch to it and use the Engineer';
  }

  @override
  String get chatWithheldNotice =>
      'This response was withheld by a safety check.';

  @override
  String get chatBlockedNotice =>
      'AURA did not answer this request: it was stopped by a safety check.';

  @override
  String get chatFeatureUnavailableNotice =>
      'This needs a feature that isn\'t available yet. AURA can answer plain questions right now. Answers that use your memory, documents, tools or images are coming soon.';

  @override
  String get chatPersonaOrchestrator => 'Orchestrator';

  @override
  String get chatPersonaTaskPlanner => 'Task Planner';

  @override
  String get chatPersonaFinanceAdvisor => 'Finance Advisor';

  @override
  String get chatPersonaHealthCompanion => 'Health Companion';

  @override
  String get chatPersonaResearchAgent => 'Research Agent';

  @override
  String get chatPersonaCodingAgent => 'Coding Agent';

  @override
  String get chatPersonaDecisionAgent => 'Decision Agent';

  @override
  String get chatPersonaEngineer => 'Engineer';

  @override
  String chatHeaderStatus(String model, String status, String latency) {
    return 'Active • $model ($status) • ${latency}ms';
  }

  @override
  String get memoryFilterAll => 'All';

  @override
  String get memoryEmptyTitle => 'No memories yet';

  @override
  String get memoryEmptyMessage =>
      'Save a fact with a kind so Copilot can use it later.';

  @override
  String get memoryForgotten => 'Memory forgotten';

  @override
  String get memoryForgottenLocally =>
      'Forgotten on this device. AURA will finish removing it from the server the next time it can reach it.';

  @override
  String memoryForgetFailed(String reason) {
    return 'Could not forget this memory. $reason';
  }

  @override
  String get memoryAddTitle => 'Remember this';

  @override
  String get memoryFactLabel => 'Fact';

  @override
  String get memorySubjectLabel => 'Subject (optional)';

  @override
  String get memoryKindField => 'Kind';

  @override
  String get memoryKindWorking => 'working';

  @override
  String get memoryKindEpisodic => 'episodic';

  @override
  String get memoryKindSemantic => 'semantic';

  @override
  String get memoryKindEntity => 'entity';

  @override
  String get memoryKindProcedural => 'procedural';

  @override
  String get memoryKindReflection => 'reflection';

  @override
  String get projectsWelcomeBack => 'Welcome back,';

  @override
  String get projectsDefaultUserName => 'Aura User';

  @override
  String get projectsTitle => 'Projects';

  @override
  String get projectsSubtitle => 'Organize your work into projects';

  @override
  String get projectsNew => 'New Project';

  @override
  String get projectsEmptyTitle => 'No projects yet';

  @override
  String get projectsEmptyMessage => 'Create your first project to get started';

  @override
  String get projectsCreateTitle => 'Create Project';

  @override
  String get projectsTitleField => 'Title *';

  @override
  String get projectsDescriptionField => 'Description';

  @override
  String get projectsStatusField => 'Status';

  @override
  String get projectsSetTargetDate => 'Set target date (optional)';

  @override
  String projectsTarget(String date) {
    return 'Target: $date';
  }

  @override
  String get projectsCreate => 'Create';

  @override
  String projectsDue(String date) {
    return 'Due: $date';
  }

  @override
  String get projectsStatusActive => 'active';

  @override
  String get projectsStatusCompleted => 'completed';

  @override
  String get projectsStatusArchived => 'archived';

  @override
  String get projectsStatusOnHold => 'on hold';

  @override
  String get analyticsTitle => 'System Analytics Dashboard';

  @override
  String get analyticsEmptyTitle => 'No telemetry metrics collected yet';

  @override
  String get analyticsEmptyMessage =>
      'Metrics will appear once you stream prompts.';

  @override
  String get analyticsTotalRequests => 'Total Requests';

  @override
  String get analyticsAvgLatency => 'Avg Latency';

  @override
  String get analyticsFailovers => 'Failovers';

  @override
  String get analyticsErrorsLogged => 'Errors Logged';

  @override
  String get analyticsLatencyHistory =>
      'Latency History (Last 10 Request Streams)';

  @override
  String get analyticsInsufficientData => 'Insufficient data';

  @override
  String get analyticsInferencePaths => 'Inference Path Distribution';

  @override
  String get analyticsNoInferenceData => 'No inference data';

  @override
  String get analyticsFailoverStats => 'API Failover Statistics';

  @override
  String get analyticsNoFailoverEvents => 'No failover events';

  @override
  String analyticsSuccessCount(int count) {
    return 'Success ($count)';
  }

  @override
  String analyticsFailoversCount(int count) {
    return 'Failovers ($count)';
  }

  @override
  String get analyticsLatestLogs => 'Latest Telemetry Logs';

  @override
  String get lifeHubFilterAll => 'All';

  @override
  String get lifeHubFilterTasks => 'Tasks';

  @override
  String get lifeHubFilterCalendar => 'Calendar';

  @override
  String get lifeHubFilterFinance => 'Finance';

  @override
  String get lifeHubFilterHealth => 'Health';

  @override
  String get lifeHubFilterProjects => 'Projects';

  @override
  String get lifeHubFilterGoals => 'Goals';

  @override
  String get lifeHubAgendaEmptyTitle => 'Nothing on the board';

  @override
  String get lifeHubAgendaEmptyMessage =>
      'Add a task, event, or log to see this week in one place.';

  @override
  String get lifeHubHealthLogTitle => 'Today’s health log';

  @override
  String lifeHubHealthLogWater(int ml) {
    return '$ml ml water';
  }

  @override
  String get lifeHubNoUpcomingEvents => 'No upcoming events';

  @override
  String get lifeHubProjectsEmptyMessage =>
      'Create a project to organize your work.';

  @override
  String get lifeHubGoalsEmptyTitle => 'No goals yet';

  @override
  String get lifeHubGoalsEmptyMessage =>
      'Create a goal to track your aspirations.';

  @override
  String lifeHubGoalProgress(int percent) {
    return '$percent% complete';
  }

  @override
  String get voiceLiveTranscript => 'Transcript';

  @override
  String get voiceLiveInterrupt => 'Interrupt';

  @override
  String get voiceLiveEndShort => 'End';

  @override
  String get voiceLiveUnsupported =>
      'Live voice is available on Android and iOS only';

  @override
  String get voiceLiveAudioUnavailable =>
      'Audio playback is unavailable on this device. Replies are shown as text only.';

  @override
  String get homeAskAuraLabel => 'Ask AURA anything';

  @override
  String get homeSendToAura => 'Send to AURA';

  @override
  String get homeInsightsLoadFailed => 'Could not load insights';

  @override
  String get homeInsightsLoadFailedMessage =>
      'Pull to refresh when you are back online.';

  @override
  String get homeDismissInsight => 'Dismiss insight';

  @override
  String homeDueTask(String title) {
    return 'Task: $title';
  }

  @override
  String homeDueEvent(String title) {
    return 'Event: $title';
  }

  @override
  String homeDueOverdue(String item) {
    return '$item, overdue';
  }

  @override
  String get notificationsFailoverTitle => 'Server inference failover';

  @override
  String notificationsFailoverMessage(String model) {
    return 'AURA could not complete the request on $model and recorded a failover.';
  }

  @override
  String get notificationsRequestFailedTitle => 'Request failed';

  @override
  String notificationsRequestFailedMessage(String model) {
    return 'An AI request failed on $model.';
  }

  @override
  String get notificationsEmptyTitle => 'Nothing to report';

  @override
  String get notificationsEmptyMessage =>
      'You\'ll see AI failovers and request errors here.';

  @override
  String get notificationsLoadFailed => 'Couldn\'t load notifications';

  @override
  String get feedbackEmptyTitle => 'No corrections yet';

  @override
  String get feedbackEmptyMessage =>
      'Rate a Copilot answer to help AURA learn what you prefer.';

  @override
  String feedbackRated(int rating, String date) {
    return 'Rated $rating/5 · $date';
  }

  @override
  String modelsLastResponse(String ms) {
    return 'Last response in $ms ms';
  }

  @override
  String get modelsNoRequestsYet => 'No requests yet this session';

  @override
  String get modelsCheckAgain => 'Check again';

  @override
  String get modelsServerNote =>
      'Requests are processed on AURA’s servers. There is nothing to download or configure on this device.';

  @override
  String get paletteOpenDesktopIde => 'Open Desktop IDE';

  @override
  String get paletteSearchHint => 'Search AURA, or jump to a screen…';

  @override
  String get paletteGoTo => 'Go to';

  @override
  String get paletteNoMatches => 'No matches';

  @override
  String get authAttestationDebugOnly =>
      'Debug marker only — hardware attestation is not enabled in this app';

  @override
  String get authAttestationReady => 'Hardware attestation token ready';

  @override
  String get authAttestationUnavailable =>
      'Hardware attestation unavailable on this device';

  @override
  String get authAuthorizedUser => 'Authorized User';

  @override
  String get authAttestationHeading => 'Device Attestation Status';

  @override
  String get authAttestationExplainer =>
      'This client does not request Google Play Integrity or Apple App Attest tokens. Debug builds send an explicit development marker; release builds advertise that hardware attestation is unsupported. Backend verification of real Play Integrity tokens exists, but this app cannot produce them without a vendor SDK and Play Console keys.';

  @override
  String get authSessionOperations => 'Session Operations';

  @override
  String get hostdCodeCommandHardDenied =>
      'This command is blocked in every workspace because it could cause serious damage.';

  @override
  String get hostdCodeDestructivePathOutsideWorkspace =>
      'This command deletes or changes files outside the workspace, so it was refused, even though the workspace is trusted.';

  @override
  String get hostdCodeDestructiveAfterLeavingWorkspace =>
      'This command leaves the workspace folder and then deletes or changes files, so it was refused, even though the workspace is trusted.';

  @override
  String get hostdCodeUntrustedShellSyntax =>
      'An untrusted workspace only runs one simple read-only command: no chaining (&&, ;, |), redirection or substitution.';

  @override
  String get hostdCodeCommandNeedsApproval =>
      'This workspace isn\'t trusted, so this command is waiting for your approval.';

  @override
  String get hostdCodeCommandNotAllowlisted =>
      'This workspace isn\'t trusted and this command isn\'t a read-only one. Trust the workspace, or open the Desktop IDE to approve it.';

  @override
  String get hostdCodeWriteNeedsApproval =>
      'This workspace isn\'t trusted, so this file change is waiting for your approval.';

  @override
  String get hostdCodeProtectedGitHooks =>
      'Writing under .git/hooks is never allowed: a hook is a program Git runs on the next commit, checkout or push.';

  @override
  String get hostdCodeProtectedGitConfig =>
      'Writing to .git/config is never allowed: it can make Git run another program.';

  @override
  String get hostdCodeRootNotADirectory =>
      'That folder doesn\'t exist, or it isn\'t a folder.';

  @override
  String get hostdCodeRootIsFilesystemRoot =>
      'A whole drive can\'t be a workspace. Choose a project folder inside it instead.';

  @override
  String get hostdCodeRootDenylisted =>
      'This system folder can never be a workspace. Choose a project folder inside it instead.';

  @override
  String get hostdCodeOutsideWorkspace =>
      'That path is outside the workspace. Add it as its own workspace to use it.';

  @override
  String get hostdCodeUnauthorized =>
      'The Host Daemon refused the request because its access key didn\'t match. Restart the Host Daemon and try again.';

  @override
  String hostdCodeInternalError(String detail) {
    return 'The Host Daemon ran into a problem: $detail';
  }

  @override
  String get hostdCodeMissingParameter =>
      'The request to the Host Daemon was missing something it needs. Update the app and try again.';

  @override
  String get hostdCodeUnknownAction =>
      'The Host Daemon doesn\'t know this action. It may be out of date.';

  @override
  String get hostdCodeRootNotGranted =>
      'This folder isn\'t one of your workspaces anymore. Add it again and try again.';

  @override
  String get hostdCodePathNotFound => 'That file or folder doesn\'t exist.';

  @override
  String get hostdCodeFileNotUtf8 =>
      'This file isn\'t text, so it can\'t be shown here.';

  @override
  String get hostdCodeApprovalNotFound =>
      'This request was already answered or has expired.';

  @override
  String get hostdCodeNotAGitRepository =>
      'This folder isn\'t a Git repository.';

  @override
  String get hostdCodeGitOutputNotUtf8 =>
      'Git returned something that isn\'t text, so it can\'t be shown here.';

  @override
  String hostdCodeGitCommandFailed(String detail) {
    return 'Git failed: $detail';
  }

  @override
  String get hostdCodeUnknownDiagnosticsTool =>
      'The Host Daemon doesn\'t know this diagnostics tool.';

  @override
  String get hostdCodeTaskNotFound =>
      'This agent task\'s workspace no longer exists.';

  @override
  String hostdCodePairingFailed(String detail) {
    return 'Couldn\'t start pairing with the server: $detail';
  }

  @override
  String get hostdCodeNoPairingSession =>
      'No pairing is in progress. Start pairing again.';

  @override
  String get hostdCodePairingEnded => 'Pairing ended before it was approved.';

  @override
  String get hostdCodeWorkspaceNotTrusted =>
      'This workspace isn\'t trusted. Trust it first to open a terminal or debugger here.';

  @override
  String hostdCodeTerminalStartFailed(String detail) {
    return 'Couldn\'t start the terminal: $detail';
  }

  @override
  String hostdCodeDebugStartFailed(String detail) {
    return 'Couldn\'t start the debugger (debugpy): $detail';
  }

  @override
  String hostdCodeDebugUnreachable(String detail) {
    return 'The debugger (debugpy) started but couldn\'t be reached: $detail';
  }

  @override
  String get coreEngineerWorkspacesLoadFailed =>
      'Could not load your workspaces. Your work is not lost -- try again shortly.';

  @override
  String get voiceSocketUnavailable =>
      'Live voice is not available on this server.';

  @override
  String get voiceSocketTooMany =>
      'Live voice is already open on another device, or you have started it too often. Close it elsewhere or wait a few minutes, then try again.';

  @override
  String get voiceSocketBadRequest =>
      'The server could not start live voice with these settings.';

  @override
  String get voiceClientNothingSpeakable =>
      'Nothing in this reply can be read aloud.';

  @override
  String get voiceClientCouldNotRead => 'Could not read this reply aloud.';

  @override
  String get hostStatusRepairRequired => 'Re-pair required';

  @override
  String get engineeringDaemonRepairRequired =>
      'Re-pair required: the server no longer accepts this device';

  @override
  String knowledgeErrTooLarge(String kind, String limit) {
    String _temp0 = intl.Intl.selectLogic(kind, {
      'pdf': 'This PDF',
      'other': 'This document',
    });
    return '$_temp0 is larger than the server\'s $limit limit.';
  }

  @override
  String get knowledgeErrRateLimited =>
      'Too many uploads right now. Wait a minute, then try again.';

  @override
  String get knowledgeErrUnavailable =>
      'The knowledge library is unavailable right now. Try again later.';

  @override
  String knowledgeErrInProgress(String kind) {
    String _temp0 = intl.Intl.selectLogic(kind, {
      'pdf': 'this PDF',
      'other': 'this document',
    });
    return 'AURA is still saving $_temp0. Refresh in a moment before trying again.';
  }

  @override
  String knowledgeErrTimeout(String kind) {
    String _temp0 = intl.Intl.selectLogic(kind, {
      'pdf': 'this PDF',
      'other': 'this document',
    });
    return 'AURA is taking a long time with $_temp0. It may still be saved: refresh to check. Trying again will not add it twice.';
  }

  @override
  String get knowledgeErrNetwork =>
      'Could not reach AURA. Trying again will not add it twice.';

  @override
  String get knowledgeErrSignIn => 'Sign in again to continue.';

  @override
  String get knowledgeErrForbidden =>
      'You do not have permission to add documents.';

  @override
  String knowledgeErrServer(String kind) {
    String _temp0 = intl.Intl.selectLogic(kind, {
      'pdf': 'this PDF',
      'other': 'this document',
    });
    return 'AURA could not save $_temp0. Trying again will not add it twice.';
  }

  @override
  String knowledgeErrRejected(String kind, String reason) {
    String _temp0 = intl.Intl.selectLogic(kind, {
      'pdf': 'this PDF',
      'other': 'this document',
    });
    return 'Could not add $_temp0: $reason';
  }

  @override
  String get knowledgeAlreadyUnchanged =>
      'Already in your library, unchanged. Nothing new was saved.';

  @override
  String get knowledgeNotSavedQuarantined =>
      'Not saved: every passage was held back by the safety check.';

  @override
  String knowledgeNotSavedReason(String reason) {
    return 'Not saved ($reason).';
  }

  @override
  String get hostRepairExplanation =>
      'The server no longer accepts this device (expired, revoked, or your account lacks Engineer access). Pair again to reconnect.';

  @override
  String knowledgeDeleteFailedReason(String reason) {
    return 'Could not delete this document: $reason';
  }

  @override
  String get knowledgeDeleteUnreachable =>
      'Could not reach AURA to delete this document.';

  @override
  String get homeInsightDismissFailed =>
      'Could not dismiss this insight. Try again shortly.';

  @override
  String get engineerConsentTitle => 'The Engineer uses Claude, by Anthropic';

  @override
  String get engineerConsentBodyWhat =>
      'To work on your code, the Engineer sends what it reads and runs to Claude, an AI model run by Anthropic, a company outside AURA.';

  @override
  String get engineerConsentBodyIncludes =>
      'That includes the messages you send the Engineer, the contents of files it opens in folders you granted or repositories you connected, and the output of commands it runs — which can include anything those commands print.';

  @override
  String get engineerConsentBodyChoice =>
      'Nothing is sent until you agree. You can withdraw in Settings → Engineer at any time; the Engineer then stops until you agree again. Other AURA features are not affected.';

  @override
  String get engineerConsentAgree => 'Agree and continue';

  @override
  String get engineerConsentNotNow => 'Not now';

  @override
  String engineerConsentSaveFailed(String reason) {
    return 'Couldn’t save your answer. $reason';
  }

  @override
  String get engineerConsentUpdateTitle => 'Update AURA to use the Engineer';

  @override
  String get engineerConsentUpdateBody =>
      'The notice about what the Engineer sends to Anthropic has changed, and this version of the app can’t show the new one. Update the app to read it and continue.';

  @override
  String get engineerConsentUpdateOk => 'OK';

  @override
  String get engineerConsentNeededNotice =>
      'The Engineer didn’t run this: it needs your agreement before it sends anything to Anthropic. Nothing was sent to Anthropic.';

  @override
  String engineerLimitReached(String time) {
    return 'You’ve reached today’s Engineer limit. It resets at $time.';
  }

  @override
  String get engineerLimitReachedNoTime =>
      'You’ve reached today’s Engineer limit. Try again after it resets.';

  @override
  String get engineerConsentStatusTitle => 'Sharing code with Anthropic';

  @override
  String engineerConsentStatusOn(String date) {
    return 'Sharing code with Anthropic: on since $date';
  }

  @override
  String get engineerConsentStatusOnNoDate => 'Sharing code with Anthropic: on';

  @override
  String get engineerConsentStatusOff => 'Sharing code with Anthropic: off';

  @override
  String get engineerConsentStatusOnSubtitle =>
      'The Engineer sends what it reads and runs to Claude';

  @override
  String get engineerConsentStatusOffSubtitle =>
      'The Engineer asks before it sends anything';

  @override
  String get engineerConsentStatusLoading => 'Checking…';

  @override
  String get engineerConsentStatusError => 'Couldn’t check. Tap to try again.';

  @override
  String get engineerConsentWithdraw => 'Withdraw';

  @override
  String get engineerConsentWithdrawTitle =>
      'Stop sharing code with Anthropic?';

  @override
  String get engineerConsentWithdrawBody =>
      'The Engineer stops until you agree again. Other AURA features are not affected.';

  @override
  String get engineerConsentWithdrawn =>
      'Withdrawn. The Engineer will ask before it sends anything again.';

  @override
  String engineerConsentWithdrawFailed(String reason) {
    return 'Couldn’t withdraw. $reason';
  }

  @override
  String get desktopIdeUnavailableTitle =>
      'The desktop IDE isn\'t available on this platform yet';

  @override
  String get desktopIdeUnavailableBody =>
      'It runs in AURA on Windows and Linux. Everything else in AURA works here as usual.';

  @override
  String get desktopIdeUnavailableGoHome => 'Go to Home';

  @override
  String get hostdRemoteControlTitle =>
      'Allow control from my phone and the web';

  @override
  String get hostdRemoteControlExplanation =>
      'When on, the Engineer can work on this computer\'s granted folders from your phone or the web. Every change and command is approved on that device first. Turn it off here any time.';

  @override
  String get hostdRemoteControlConfirmTitle =>
      'Allow control from your phone and the web?';

  @override
  String get hostdRemoteControlConfirmBody =>
      'Anyone signed in to your AURA account on a phone or in a browser will be able to ask the Engineer to read, change and run commands in this computer\'s granted folders. Each change and command still waits for approval on that device. You can turn this off here at any time.';

  @override
  String get hostdRemoteControlConfirmAllow => 'Allow';

  @override
  String hostdRemoteControlFailed(String error) {
    return 'Could not change remote control: $error';
  }

  @override
  String get engineerDesktopNotConnected => 'Your computer isn\'t connected';

  @override
  String get engineerDesktopRemoteOff =>
      'Remote control is off on your computer — turn it on in AURA on the computer';

  @override
  String get engineerDesktopNoFolders =>
      'Your computer hasn\'t shared any folders yet — add one in AURA on the computer';

  @override
  String get engineerDesktopFolderLabel => 'Folder on your computer';

  @override
  String get engineerDesktopCheckFailed => 'Couldn\'t check your computer';

  @override
  String get engineerDesktopRefresh => 'Check again';

  @override
  String get hostdCodeGitPushNeedsApproval =>
      'Any git push by the agent waits for your approval.';

  @override
  String get hostdCodePathOutsideTrustedFolder =>
      'This command reaches outside this trusted folder, so it waits for your approval.';

  @override
  String get hostdCodeRemoteCommandNeedsConfirmation =>
      'Commands sent from your phone or the web wait for your approval there first.';

  @override
  String get hostdCodeProtectedGitDirFile =>
      'Writing a .git file that points Git somewhere else is always refused.';

  @override
  String get hostdCodeRemoteControlDisabled =>
      'Remote control is off on this computer. Turn on \"Allow control from my phone and the web\" on the computer.';

  @override
  String get hostdCodeRemoteConfirmationRequired =>
      'Changes from your phone or the web must be approved there first.';

  @override
  String get hostdCodeInvalidTaskKey =>
      'The Host Daemon didn\'t recognize this task. Update the app and try again.';

  @override
  String get hostdCodeIsolatedTaskUnresolved =>
      'Couldn\'t set up an isolated copy of the folder for this task.';

  @override
  String get hostdCodeInvalidOrigin =>
      'The Host Daemon couldn\'t tell where this request came from, so it refused it. Update the app and try again.';

  @override
  String get hostdCodeDiagnosticsUntrusted =>
      'Problems runs only in trusted folders. Trust this folder to check it.';

  @override
  String get hostdCodeInvalidRequestBody =>
      'The request to the Host Daemon wasn\'t in the form it expects. Update the app and try again.';

  @override
  String get gitInitBannerTitle => 'This folder isn\'t a git repository';

  @override
  String get gitInitBannerBody =>
      'The Engineer works in its own copy of the folder so every change can be reviewed and undone, and that needs git. Initialize git here to let it start.';

  @override
  String get gitInitButton => 'Initialize git here';

  @override
  String get gitInitButtonShort => 'Initialize git';

  @override
  String get gitInitConfirmTitle => 'Initialize git in this folder?';

  @override
  String gitInitConfirmBody(String path) {
    return 'This runs git init in $path and commits the folder\'s current files as a first commit, on this computer only. Files your .gitignore lists are left out; everything else — including files like .env — goes into that local commit, so add a .gitignore first if you need one.';
  }

  @override
  String get gitInitConfirmAction => 'Initialize';

  @override
  String get gitInitDone => 'Git is ready in this folder.';

  @override
  String gitInitFailed(String error) {
    return 'Couldn\'t initialize git: $error';
  }

  @override
  String get engineerDesktopNotGit =>
      'This folder isn\'t a git repository yet — the Engineer needs one to work on its own copy.';

  @override
  String get hostdCodeIsolationNeedsGit =>
      'This folder isn\'t a git repository, so the Engineer can\'t work on its own copy of it — use Initialize git here first.';

  @override
  String get hostdCodeAlreadyAGitRepo =>
      'This folder is already a git repository.';

  @override
  String hostdCodeGitInitFailed(String detail) {
    return 'Couldn\'t initialize git here: $detail';
  }

  @override
  String get hostdCodeApprovalQueueFull =>
      'Too many changes are waiting for your approval; answer some first.';

  @override
  String get chatApprovalExpiredNotice => 'This approval expired. Ask again.';

  @override
  String get ideStatusRepairTooltip =>
      'The server no longer accepts this computer. Click to pair it again.';

  @override
  String get explorerRevealWindowsOnly =>
      'Reveal in filesystem is only supported on Windows for now';

  @override
  String get loginApple => 'Continue with Apple';

  @override
  String get loginEmail => 'Continue with email';

  @override
  String get emailFieldLabel => 'Email';

  @override
  String get passwordFieldLabel => 'Password';

  @override
  String get confirmPasswordFieldLabel => 'Confirm password';

  @override
  String get nameFieldLabel => 'Name';

  @override
  String get emailSignInAction => 'Sign in';

  @override
  String get emailCreateAccountAction => 'Create account';

  @override
  String get emailForgotPassword => 'Forgot password?';

  @override
  String get emailSwitchToRegister => 'New to AURA? Create an account';

  @override
  String get emailSwitchToSignIn => 'Already have an account? Sign in';

  @override
  String get emailSendReset => 'Send reset link';

  @override
  String get emailResetSent =>
      'If an account exists for that address, we\'ve sent a reset link.';

  @override
  String get emailInvalid => 'Enter a valid email address.';

  @override
  String get passwordTooShort => 'Use at least 8 characters.';

  @override
  String get passwordsDontMatch => 'The passwords don\'t match.';

  @override
  String get verifyEmailBanner =>
      'Check your inbox to verify your email. You\'ll need it to create or join a team.';

  @override
  String get verifyEmailResend => 'Resend';

  @override
  String get identityWrongCredentials =>
      'That email and password don\'t match an account.';

  @override
  String get identityEmailInUse =>
      'An account already uses this email. Sign in instead.';

  @override
  String get identityWeakPassword => 'Choose a stronger password.';

  @override
  String get identityInvalidEmail => 'That email address isn\'t valid.';

  @override
  String get identityNetwork =>
      'Couldn\'t reach the sign-in service. Check your connection.';

  @override
  String get identityDifferentMethod =>
      'This email already uses another sign-in method. Sign in with that method instead.';

  @override
  String get identityUnavailable =>
      'This sign-in method isn\'t available right now.';

  @override
  String get verifyEmailDone => 'I\'ve verified';

  @override
  String get workspaceTileTitle => 'Workspace';

  @override
  String get workspacePersonal => 'Personal';

  @override
  String get workspaceSwitcherTitle => 'Switch workspace';

  @override
  String get workspaceCreateTeam => 'Create a team';

  @override
  String get workspaceTeamSettings => 'Team settings';

  @override
  String get workspaceRoleOwner => 'Owner';

  @override
  String get workspaceRoleAdmin => 'Admin';

  @override
  String get workspaceRoleMember => 'Member';

  @override
  String get teamNameLabel => 'Team name';

  @override
  String get teamCreateAction => 'Create';

  @override
  String get teamMembersSection => 'Members';

  @override
  String get teamInvitesSection => 'Pending invites';

  @override
  String get teamYou => 'You';

  @override
  String get teamInviteAction => 'Invite';

  @override
  String get teamInviteEmailLabel => 'Email address';

  @override
  String get teamInviteSentTitle => 'Invite created';

  @override
  String teamInviteSentBody(String email) {
    return 'Send this link to $email. It works once and expires in a few days. If email is set up for AURA, we sent it too.';
  }

  @override
  String get teamCopyLink => 'Copy link';

  @override
  String get teamLinkCopied => 'Link copied';

  @override
  String get teamMakeAdmin => 'Make admin';

  @override
  String get teamMakeMember => 'Make member';

  @override
  String get teamMakeOwner => 'Make owner';

  @override
  String get teamRemove => 'Remove from team';

  @override
  String get teamRevokeInvite => 'Revoke';

  @override
  String teamMakeOwnerConfirm(String who) {
    return 'Make $who the owner? You\'ll become an admin, and only they can manage billing or delete the team.';
  }

  @override
  String teamRemoveConfirm(String who) {
    return 'Remove $who from the team?';
  }

  @override
  String get teamLeave => 'Leave team';

  @override
  String teamLeaveConfirm(String name) {
    return 'Leave $name? You\'ll lose access to its shared documents and chats.';
  }

  @override
  String get teamDelete => 'Delete team';

  @override
  String teamDeleteConfirm(String name) {
    return 'Delete $name for everyone? Its shared documents and chats are removed. This can\'t be undone.';
  }

  @override
  String get teamReadOnlyNote =>
      'Only the owner and admins can invite or change members.';

  @override
  String get planTitle => 'Plan and usage';

  @override
  String planCurrent(String plan) {
    return 'Plan: $plan';
  }

  @override
  String planMessages(String used, String allowed) {
    return 'Messages this month: $used of $allowed';
  }

  @override
  String planResets(String date) {
    return 'Resets on $date';
  }

  @override
  String planStorage(String used, String allowed) {
    return 'Knowledge storage: $used of $allowed';
  }

  @override
  String get planEngineerIncluded => 'Engineer agent included';

  @override
  String get planEngineerNotIncluded => 'Engineer agent: Pro and Team plans';

  @override
  String planSeats(String seats) {
    return 'Seats: $seats';
  }

  @override
  String planSubscriptionStatus(String status) {
    return 'Subscription: $status';
  }

  @override
  String planPaidThrough(String date) {
    return 'Paid through $date';
  }

  @override
  String planGraceUntil(String date) {
    return 'Full access until $date, then the Free plan';
  }

  @override
  String get planPaymentsSection => 'Payments';

  @override
  String get planUpgradeSection => 'Upgrade';

  @override
  String get planUpgradePro => 'Get Pro';

  @override
  String get planUpgradeTeam => 'Get Team';

  @override
  String get planSeatsLabel => 'Seats';

  @override
  String get planNameFree => 'Free';

  @override
  String get planNamePro => 'Pro';

  @override
  String get planNameTeam => 'Team';

  @override
  String get planSeatsInvalid => 'Enter a whole number of seats, 1 or more.';

  @override
  String get subscriptionStatusPending => 'Awaiting payment';

  @override
  String get subscriptionStatusActive => 'Active';

  @override
  String get subscriptionStatusPastDue => 'Payment overdue';

  @override
  String get subscriptionStatusGrace => 'In grace period';

  @override
  String get subscriptionStatusCanceled => 'Canceled';

  @override
  String get subscriptionStatusExpired => 'Expired';

  @override
  String get planTeamNotInApp => 'Team plans can\'t be bought in this app.';

  @override
  String planStoreBuy(String price) {
    return 'Upgrade to Pro · $price';
  }

  @override
  String get planStoreWaiting => 'Purchase received. Activating your plan…';

  @override
  String get planStorePending =>
      'Purchase received. Your plan will update in a few minutes.';

  @override
  String get planStoreManage => 'Manage subscription';

  @override
  String get planStoreOtherDevice =>
      'Your subscription is managed in your other device\'s app store.';

  @override
  String get planStoreElsewhere =>
      'This subscription is managed in the App Store or Google Play.';

  @override
  String get planFawryActive => 'You already have a subscription.';

  @override
  String get planStoreFailed => 'The purchase didn\'t go through.';

  @override
  String get planOwnerOnly => 'Only the workspace owner can change the plan.';

  @override
  String get planBillingOff => 'Upgrades are not available yet.';

  @override
  String get planCancel => 'Cancel subscription';

  @override
  String get planCancelConfirm =>
      'Cancel the subscription? You keep your plan until the end of what you paid for.';

  @override
  String get checkoutTitle => 'Pay with Fawry';

  @override
  String checkoutReference(String reference) {
    return 'Reference number: $reference';
  }

  @override
  String checkoutAmount(String amount) {
    return 'Amount: $amount';
  }

  @override
  String checkoutExpires(String date) {
    return 'Pay before $date';
  }

  @override
  String get checkoutHowTo =>
      'Pay at any Fawry outlet, from your mobile wallet, or in the myFawry app using this reference number. Your plan starts as soon as the payment arrives.';

  @override
  String get seePlans => 'See plans';

  @override
  String get inviteAcceptTitle => 'Join a team';

  @override
  String get inviteAcceptBody => 'You\'ve been invited to join a team on AURA.';

  @override
  String get inviteAcceptAction => 'Join';

  @override
  String inviteAccepted(String name) {
    return 'You joined $name';
  }

  @override
  String get inviteInvalid =>
      'This invite is no longer valid. Ask for a new one.';

  @override
  String get inviteNeedsVerifiedEmail =>
      'Verify your email address first, then open the link again.';

  @override
  String get shareWithTeam => 'Share with team';

  @override
  String sharedWithTeam(String name) {
    return 'Shared with $name';
  }

  @override
  String get teamChatsTitle => 'Team chats';

  @override
  String get teamChatsEmpty => 'No chats have been shared with this team yet.';

  @override
  String teamChatsSharedBy(String who) {
    return 'Shared by $who';
  }

  @override
  String get teamChatsUnshare => 'Unshare';

  @override
  String get teamChatsTooMany =>
      'You\'ve shared the most chats allowed. Unshare one first.';

  @override
  String get signInMethodsTitle => 'Sign-in methods';

  @override
  String get signInMethodsSubtitle => 'Google, Apple, email';

  @override
  String get signInMethodLinked => 'Connected';

  @override
  String get signInMethodLink => 'Connect';

  @override
  String get signInMethodInUse =>
      'That sign-in already belongs to another AURA account.';

  @override
  String get signInMethodLinkedDone => 'Sign-in method connected';

  @override
  String get legalAgreePrefix => 'By continuing you agree to the';

  @override
  String get legalTerms => 'Terms of Service';

  @override
  String get legalPrivacy => 'Privacy Policy';

  @override
  String get legalRefund => 'Refund Policy';

  @override
  String get deleteBlockedTitle => 'You own a team';

  @override
  String get deleteBlockedBody =>
      'Before deleting your account, make someone else the owner of these teams, or delete them:';

  @override
  String get shareNothing => 'This chat has nothing to share yet.';

  @override
  String shareConfirm(String name) {
    return 'Share a copy of this chat with $name? Everyone in the team can read it.';
  }

  @override
  String get twoStepTitle => 'Two-step verification';

  @override
  String get twoStepSignInBody => 'Enter the code from your authenticator app.';

  @override
  String get twoStepCodeLabel => '6-digit code';

  @override
  String get twoStepVerify => 'Verify';

  @override
  String get twoStepWrongCode =>
      'That code didn\'t match. Use the newest code from your authenticator app.';

  @override
  String get twoStepUnsupported =>
      'This account\'s second sign-in step isn\'t supported in this app. Contact support to reset it.';

  @override
  String get identityReauthRequired =>
      'For your security, sign out and sign in again, then try once more.';

  @override
  String get identityWrongCode => 'That code didn\'t match.';

  @override
  String get twoStepIntro =>
      'Add a code from an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password…) to every sign-in.';

  @override
  String get twoStepVerifyEmailFirst =>
      'Verify your email address first, then come back to turn this on.';

  @override
  String get twoStepSetUp => 'Set up';

  @override
  String get twoStepScanOrEnter =>
      'Add this key to your authenticator app, then type the 6-digit code it shows.';

  @override
  String get twoStepOpenApp => 'Open in authenticator app';

  @override
  String get twoStepCopyKey => 'Copy key';

  @override
  String get twoStepOn =>
      'On: sign-ins ask for a code from your authenticator app.';

  @override
  String get twoStepTurnOff => 'Turn off';

  @override
  String get twoStepTurnOffConfirm =>
      'Sign-ins will no longer ask for a code. Turn off two-step verification?';

  @override
  String get twoStepEnabled => 'Two-step verification is on.';

  @override
  String get twoStepNoApp =>
      'No authenticator app opened it. Copy the key into your authenticator app instead.';

  @override
  String get twoStepAuthenticatorApp => 'Authenticator app';

  @override
  String get loginBrowserHandoff =>
      'Continue in your browser — Google, Apple or email';

  @override
  String coreDesktopSignInBrowserError(String error) {
    return 'The browser sign-in reported an error: $error';
  }

  @override
  String get coreDesktopSignInNoSession =>
      'The AURA server did not return a session.';

  @override
  String get desktopSignInTitle => 'Sign in to the AURA desktop app';

  @override
  String get desktopSignInSubtitle =>
      'Choose how to sign in. When you\'re done, this tab hands the sign-in back to the desktop app.';

  @override
  String get desktopSignInSharedComputer =>
      'AURA signs you out of this browser again once the desktop app has your sign-in.';

  @override
  String get desktopSignInWorking => 'Finishing sign-in…';

  @override
  String get desktopSignInDone =>
      'Signed in. Returning you to the AURA desktop app…';

  @override
  String get desktopSignInDoneHint =>
      'If nothing happens, switch back to the desktop app. You can close this tab.';

  @override
  String get desktopSignInInvalidLink =>
      'This sign-in link isn\'t valid. Start again from the AURA desktop app: choose \"Continue in your browser\" on its sign-in screen.';

  @override
  String get desktopSignInWebOnly =>
      'This page is for a web browser. In the desktop app, choose \"Continue in your browser\" on the sign-in screen and it opens this page for you.';

  @override
  String get desktopSignInGoToLogin => 'Go to sign-in';

  @override
  String get desktopSignInServerUnreachable =>
      'Couldn\'t reach the AURA server. Check your connection and try again.';

  @override
  String desktopSignInServerFailed(int status) {
    return 'The AURA server couldn\'t hand this sign-in to the desktop app (HTTP $status).';
  }

  @override
  String get desktopSignInNoIdentityToken =>
      'The sign-in service returned no identity token for this account. Try again.';

  @override
  String get desktopSignInReturnFailed =>
      'Couldn\'t go back to the desktop app. Make sure it\'s still waiting for you, then start again from it.';
}
