import 'dart:async';

import 'package:http/http.dart' as http;

import '../../../core/network/aura_failure.dart';

/// Prefix stored on assistant rows when no backend path could serve the turn.
/// The chat UI uses this to show the Retry card.
const kChatConnectionErrorPrefix = 'Connection Error';

const kChatConnectionErrorMessage =
    'Connection Error: Failed to retrieve response from active AI agent.';

/// Assistant-row text for a turn the backend refused for its content class
/// (`capability_unavailable`). Deliberately does not start with
/// [kChatConnectionErrorPrefix], so the transcript shows no Retry card.
const kChatFeatureUnavailableText = '$kFeatureUnavailableTitle. $kFeatureUnavailableMessage';

/// Assistant-row text for an answer the output guard withheld after it had streamed
/// (`safety_violation`, `post_hoc`). Replaces the streamed text rather than keeping it: the
/// guard exists to keep that content off the screen and out of the synced transcript.
/// Deliberately without [kChatConnectionErrorPrefix]: retrying is not the remedy.
const kChatWithheldText = 'This response was withheld by a safety check.';

/// Assistant-row text for a request a guard refused before anything was generated.
const kChatBlockedText = 'AURA did not answer this request: it was stopped by a safety check.';

/// Prefix stored on assistant rows for a turn the workspace's plan refused (HTTP 402
/// `plan_limit`, SaaS P1-C/F2). The bubble shows the plain sentence and a "See plans" action;
/// deliberately without [kChatConnectionErrorPrefix]: retrying reaches the same limit.
const kChatPlanLimitPrefix = 'Plan limit';

bool isChatPlanLimitText(String text) => text.startsWith('$kChatPlanLimitPrefix: ');

/// The row text for a plan-limit refusal, in [AuraFailure]'s words for it.
String chatPlanLimitText(Object error) {
  final failure = AuraFailure.from(error);
  return '$kChatPlanLimitPrefix: ${failure.title}. ${failure.message}';
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
