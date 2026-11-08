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
