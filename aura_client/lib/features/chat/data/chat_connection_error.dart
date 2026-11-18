import 'dart:async';

import 'package:http/http.dart' as http;
import 'package:intl/intl.dart';

import '../../../l10n/generated/app_localizations.dart';
import '../../../l10n/generated/app_localizations_en.dart';

/// Prefix stored on assistant rows when no backend path could serve the turn.
/// The chat UI uses this to show the Retry card.
const kChatConnectionErrorPrefix = 'Connection Error';

const kChatConnectionErrorMessage =
    'Connection Error: Failed to retrieve response from active AI agent.';

/// Assistant-row text for a turn the backend refused for its content class
/// (`capability_unavailable`). Deliberately does not start with
/// [kChatConnectionErrorPrefix], so the transcript shows no Retry card.
/// Stored as the row's text and matched by `message_bubble.dart`, which shows the localized
/// `chatFeatureUnavailableNotice` in its place; so it is a fixed marker, never translated (a
/// row saved while the app was in Arabic must still match after switching to English).
const kChatFeatureUnavailableText = "This needs a feature that isn't available yet. "
    'AURA can answer plain questions right now. Answers that use your memory, '
    'documents, tools or images are coming soon.';

/// Assistant-row text for an answer the output guard withheld after it had streamed
/// (`safety_violation`, `post_hoc`). Replaces the streamed text rather than keeping it: the
/// guard exists to keep that content off the screen and out of the synced transcript.
/// Deliberately without [kChatConnectionErrorPrefix]: retrying is not the remedy.
const kChatWithheldText = 'This response was withheld by a safety check.';

/// Assistant-row text for a request a guard refused before anything was generated.
const kChatBlockedText = 'AURA did not answer this request: it was stopped by a safety check.';

// The transcript stores refusal rows as text and syncs them, like the constants above, so they
// are written in the app's one shipped language. The words live in app_en.arb all the same.
final _en = AppLocalizationsEn();

/// Assistant-row text for an Engineer turn refused with `consent_required` (D-03). Not a
/// connection error, so no Retry card: the consent sheet is the remedy, and agreeing resends.
final String kChatEngineerConsentNeededText = _en.engineerConsentNeededNotice;

/// Assistant-row text for an Engineer turn refused with `user_quota_exhausted` (D-05), naming
/// the reset moment from `details.reset_at` in this device's local time. No Retry card: the
/// same turn is refused until then.
String engineerLimitReachedText(Object? details, {DateTime? now}) {
  final raw = details is Map ? details['reset_at'] : null;
  var reset = raw is String ? DateTime.tryParse(raw) : null;
  if (reset == null) return _en.engineerLimitReachedNoTime;
  // The contract says UTC. Dart parses any offset into a UTC DateTime, so a non-UTC result means
  // the timestamp had none: read it as UTC, not as this device's local time.
  if (!reset.isUtc) {
    reset = DateTime.utc(reset.year, reset.month, reset.day, reset.hour, reset.minute,
        reset.second, reset.millisecond, reset.microsecond);
  }
  return _en.engineerLimitReached(formatLimitReset(reset.toLocal(), now: now ?? DateTime.now()));
}

/// "1:00 AM" when [local] falls on [now]'s day, "Wed 1:00 AM" otherwise. Always formatted in
/// English, like the row it goes into, whatever language the app is showing.
String formatLimitReset(DateTime local, {required DateTime now}) {
  final sameDay = local.year == now.year && local.month == now.month && local.day == now.day;
  return sameDay ? DateFormat.jm('en_US').format(local) : DateFormat('EEE', 'en_US').add_jm().format(local);
}

/// The Engineer refusal rows above are stored in English (they are persisted and synced as the
/// turn's text); this is the same notice in the language on screen, or null when [content] is
/// not one of them. The limit row keeps its stored reset time and only rewraps it.
String? localizedEngineerRefusal(String content, AppLocalizations l10n) {
  if (content == kChatEngineerConsentNeededText) return l10n.engineerConsentNeededNotice;
  if (content == _en.engineerLimitReachedNoTime) return l10n.engineerLimitReachedNoTime;
  const marker = '\u0000';
  final template = _en.engineerLimitReached(marker);
  final cut = template.indexOf(marker);
  final prefix = template.substring(0, cut);
  final suffix = template.substring(cut + marker.length);
  if (content.length > prefix.length + suffix.length &&
      content.startsWith(prefix) &&
      content.endsWith(suffix)) {
    return l10n.engineerLimitReached(content.substring(prefix.length, content.length - suffix.length));
  }
  return null;
}

bool isChatConnectionErrorText(String text) =>
    text.startsWith(kChatConnectionErrorPrefix);

/// True when [error] means the host was unreachable (socket, TLS handshake,
/// connect timeout, or an [http.ClientException] wrapping the same).
bool isUnreachableBackendError(Object error) {
  if (error is TimeoutException || error is http.ClientException) {
    return true;
  }
  return _messageLooksLikeUnreachable(error.toString());
}

bool _messageLooksLikeUnreachable(String raw) {
  final message = raw.toLowerCase();
  const markers = <String>[
    'socketexception',
    'clientexception',
    'handshakeexception',
    'tlsexception',
    'timeoutexception',
    'connection refused',
    'connection reset',
    'connection timed out',
    'failed host lookup',
    'network is unreachable',
    'no route to host',
    'software caused connection abort',
    'os error: connection',
  ];
  return markers.any(message.contains);
}
