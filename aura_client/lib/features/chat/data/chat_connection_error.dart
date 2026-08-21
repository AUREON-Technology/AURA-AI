import 'dart:async';
import 'dart:io';

import 'package:http/http.dart' as http;

/// Prefix stored on assistant rows when no backend path could serve the turn.
/// The chat UI uses this to show the Retry card.
const kChatConnectionErrorPrefix = 'Connection Error';

const kChatConnectionErrorMessage =
    'Connection Error: Failed to retrieve response from active AI agent.';

bool isChatConnectionErrorText(String text) =>
    text.startsWith(kChatConnectionErrorPrefix);

/// True when [error] means the host was unreachable (socket, TLS handshake,
/// connect timeout, or an [http.ClientException] wrapping the same).
bool isUnreachableBackendError(Object error) {
  if (error is SocketException ||
      error is HandshakeException ||
      error is TlsException ||
      error is TimeoutException) {
    return true;
  }
  if (error is http.ClientException) {
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
