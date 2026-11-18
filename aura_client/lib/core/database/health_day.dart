/// The calendar day a health record belongs to (F4).
///
/// A health record is one row per calendar day. The day is stored as that date's **UTC
/// midnight**: the instant the server writes for a day its own tools log
/// (`aura_backend/aura_core/adapters/postgres_synced_personal_os.py`), and the instant every
/// device now sends. The client used to key the day on *local* midnight and send that, so in a
/// UTC+2 zone the user's "today" was `…T22:00Z` of the day before while the assistant's entry for
/// the same day was `…T00:00Z`: neither side found the other's row, and the day had two.
///
/// A day is therefore a calendar date, not an instant, and is never converted with `toLocal()`.
library;

/// The stored key for the day [moment] falls on in this device's local time: "today" when
/// [moment] is now.
DateTime healthDayFor(DateTime moment) {
  final local = moment.toLocal();
  return DateTime.utc(local.year, local.month, local.day);
}

/// The stored key for the day a `record_date` instant names, from the server or from a row
/// stored before F4.
///
/// UTC midnight is the convention, and names its own UTC date. Any other instant was keyed on
/// local midnight by a client that predates F4, and names the local calendar date it fell on
/// where it was written: this device's offset at that instant, or [localOffset] (tests).
DateTime healthDayOfInstant(DateTime instant, {Duration? localOffset}) {
  final utc = instant.toUtc();
  if (_isMidnight(utc)) return DateTime.utc(utc.year, utc.month, utc.day);
  final local = utc.add(localOffset ?? instant.toLocal().timeZoneOffset);
  return DateTime.utc(local.year, local.month, local.day);
}

/// The day [key] names, as local midnight: what a screen formats with `DateFormat`, so the date
/// shown is the stored calendar date in every time zone (UTC midnight is the previous evening
/// west of Greenwich).
DateTime healthDayForDisplay(DateTime key) {
  final utc = key.toUtc();
  return DateTime(utc.year, utc.month, utc.day);
}

bool _isMidnight(DateTime utc) =>
    utc.hour == 0 && utc.minute == 0 && utc.second == 0 && utc.millisecond == 0 && utc.microsecond == 0;
