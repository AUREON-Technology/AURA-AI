/// Maps an exception caught from an `aura_hostd` call into text a person can act on.
///
/// Without this, callers interpolated the raw exception (`'$error'`) straight into a snackbar or
/// status line -- so internal strings like a bare `HostdControlError`'s message, or Dart's own
/// `SocketException` formatting, reached the Desktop IDE's UI verbatim. Falls back to the raw
/// string only for an exception type this daemon-facing code has no better-known meaning for.
library;

import 'hostd_control_client.dart' show HostdControlError, HostdControlUnavailable;

String describeHostdError(Object error) {
  if (error is HostdControlError) {
    // Already plain language for the cases hostd_control_client_io.dart itself enriches (e.g.
    // the "no such endpoint" -> "may be out of date" hint); a daemon-reported business message
    // (a containment refusal, a missing root) is written for a person too, so passing it through
    // unchanged is correct, not a fallback.
    return error.message;
  }
  if (error is HostdControlUnavailable) {
    return 'aura_hostd is not running. Start it and try again.';
  }
  return '$error';
}
