/// Translates `aura_hostd`'s stable error codes into the language on screen.
///
/// Every refusal and error the daemon can show a person carries a snake_case `code` next to its
/// English text (`aura_hostd/lib/src/error_codes.dart`, Arabic plan stage 3, decision 12): the
/// control server's `{"error": ..., "code": ...}` bodies, terminal/debug `error` frames, and the
/// pairing status's `reason`. A daemon that predates codes sends none, and a newer daemon may send
/// one this app does not know yet; both fall back to the daemon's own English text.
library;

import 'package:aura_client/l10n/l10n.dart';

/// The text to show for a daemon error with [code] whose English text is [fallback].
///
/// [detail] is the underlying error alone (tool or system output, never translated), quoted by
/// the few messages that wrap one; it defaults to [fallback]. [l10n] defaults to [currentL10n].
String hostdCodeMessage(String? code, String fallback, {String? detail, AppLocalizations? l10n}) {
  final t = l10n ?? currentL10n;
  final quoted = detail ?? fallback;
  return switch (code) {
    'command_hard_denied' => t.hostdCodeCommandHardDenied,
    'destructive_path_outside_workspace' => t.hostdCodeDestructivePathOutsideWorkspace,
    'destructive_after_leaving_workspace' => t.hostdCodeDestructiveAfterLeavingWorkspace,
    'untrusted_shell_syntax' => t.hostdCodeUntrustedShellSyntax,
    'command_needs_approval' => t.hostdCodeCommandNeedsApproval,
    'command_not_allowlisted' => t.hostdCodeCommandNotAllowlisted,
    'write_needs_approval' => t.hostdCodeWriteNeedsApproval,
    'protected_git_hooks' => t.hostdCodeProtectedGitHooks,
    'protected_git_config' => t.hostdCodeProtectedGitConfig,
    'root_not_a_directory' => t.hostdCodeRootNotADirectory,
    'root_is_filesystem_root' => t.hostdCodeRootIsFilesystemRoot,
    'root_denylisted' => t.hostdCodeRootDenylisted,
    'outside_workspace' => t.hostdCodeOutsideWorkspace,
    'unauthorized' => t.hostdCodeUnauthorized,
    'no_such_endpoint' => t.coreHostdOutOfDate,
    'internal_error' => t.hostdCodeInternalError(quoted),
    'missing_parameter' => t.hostdCodeMissingParameter,
    'unknown_action' => t.hostdCodeUnknownAction,
    'root_not_granted' => t.hostdCodeRootNotGranted,
    'path_not_found' => t.hostdCodePathNotFound,
    'file_not_utf8' => t.hostdCodeFileNotUtf8,
    'approval_not_found' => t.hostdCodeApprovalNotFound,
    'not_a_git_repository' => t.hostdCodeNotAGitRepository,
    'git_output_not_utf8' => t.hostdCodeGitOutputNotUtf8,
    'git_command_failed' => t.hostdCodeGitCommandFailed(quoted),
    'unknown_diagnostics_tool' => t.hostdCodeUnknownDiagnosticsTool,
    'task_not_found' => t.hostdCodeTaskNotFound,
    'pairing_failed' => t.hostdCodePairingFailed(quoted),
    'no_pairing_session' => t.hostdCodeNoPairingSession,
    'pairing_ended' => t.hostdCodePairingEnded,
    'workspace_not_trusted' => t.hostdCodeWorkspaceNotTrusted,
    'terminal_start_failed' => t.hostdCodeTerminalStartFailed(quoted),
    'debug_start_failed' => t.hostdCodeDebugStartFailed(quoted),
    'debug_unreachable' => t.hostdCodeDebugUnreachable(quoted),
    // Trust-tier, remote-control and isolation refusals (WP3, WP8; D-06, D-10b, D-10c).
    'git_push_needs_approval' => t.hostdCodeGitPushNeedsApproval,
    'path_outside_trusted_folder' => t.hostdCodePathOutsideTrustedFolder,
    'remote_command_needs_confirmation' => t.hostdCodeRemoteCommandNeedsConfirmation,
    'protected_git_dir_file' => t.hostdCodeProtectedGitDirFile,
    'remote_control_disabled' => t.hostdCodeRemoteControlDisabled,
    'remote_confirmation_required' => t.hostdCodeRemoteConfirmationRequired,
    'invalid_task_key' => t.hostdCodeInvalidTaskKey,
    'isolated_task_unresolved' => t.hostdCodeIsolatedTaskUnresolved,
    'invalid_origin' => t.hostdCodeInvalidOrigin,
    'diagnostics_untrusted' => t.hostdCodeDiagnosticsUntrusted,
    'invalid_request_body' => t.hostdCodeInvalidRequestBody,
    // Isolation needs git (WP4, D-13) and the bounded approval queue (WP9).
    'isolation_needs_git' => t.hostdCodeIsolationNeedsGit,
    'already_a_git_repo' => t.hostdCodeAlreadyAGitRepo,
    'git_init_failed' => t.hostdCodeGitInitFailed(quoted),
    'approval_queue_full' => t.hostdCodeApprovalQueueFull,
    _ => fallback,
  };
}
