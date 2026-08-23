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
  String get notificationsTooltip => 'Notifications';

  @override
  String unreadNotifications(int count) {
    return '$count unread notifications';
  }

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
  String get onboardingGetStarted => 'Get started';
}
