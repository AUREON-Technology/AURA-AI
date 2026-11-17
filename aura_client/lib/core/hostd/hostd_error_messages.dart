/// Maps an exception caught from an `aura_hostd` call into text a person can act on.
///
/// Without this, callers interpolated the raw exception (`'$error'`) straight into a snackbar or
/// status line -- so internal strings like a bare `HostdControlError`'s message, or Dart's own
/// `SocketException` formatting, reached the Desktop IDE's UI verbatim. Falls back to the raw
/// string only for an exception type this daemon-facing code has no better-known meaning for.
library;

import 'package:aura_client/l10n/l10n.dart';

import 'hostd_control_client.dart' show HostdControlError, HostdControlUnavailable;

String describeHostdError(Object error) {
  if (error is HostdControlError) {
    // Already plain language: hostd_control_client_io.dart translates a daemon-reported message
    // (a containment refusal, a missing root) by its `code` (hostd_error_codes.dart) into the
    // language on screen, and keeps the daemon's English only for a daemon that predates codes
    // or a code this app does not know yet.
    return error.message;
  }
  if (error is HostdControlUnavailable) {
    return currentL10n.coreHostdNotRunningStartIt;
  }
  return '$error';
}
