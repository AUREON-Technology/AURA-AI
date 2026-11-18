import 'package:intl/intl.dart';

import '../../l10n/l10n.dart';

/// "Today 14:05", "Yesterday", "3 days ago", else a short date, in the language on screen
/// (docs/plans/arabic-language_plan.md, stage 2). Days are calendar days in local time, so
/// 23:50 yesterday is "Yesterday" at 00:10, not "Today".
///
/// [now] is for tests.
String relativeDay(AppLocalizations l10n, DateTime when, {DateTime? now}) {
  final today = now ?? DateTime.now();
  final local = when.toLocal();
  final days = DateTime(today.year, today.month, today.day)
      .difference(DateTime(local.year, local.month, local.day))
      .inDays;
  if (days <= 0) return l10n.relativeTodayAt(DateFormat.jm().format(local));
  if (days == 1) return l10n.relativeYesterday;
  if (days < 7) return l10n.relativeDaysAgo(days);
  return DateFormat.yMd().format(local);
}
