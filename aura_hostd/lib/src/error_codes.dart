/// Stable machine codes for every refusal and error this daemon can show a person.
///
/// Each one travels as a `code` field next to the English `reason`/`error`/`message` text that was
/// already there (control-server JSON bodies, terminal/debug `error` frames, the pairing status,
/// command-policy verdicts, RPC `error` frames). The English text is unchanged, so an app built
/// before codes existed keeps working; a newer app translates by `code` instead of showing the
/// English (`aura_client/lib/core/hostd/hostd_error_codes.dart`, Arabic plan stage 3, decision
/// 12). A terminal/debug `error` frame whose message wraps an underlying error also carries that
/// error alone as `detail`, so a translated message can quote it without the English prefix.
/// Terminal output of the CLI itself stays English and carries no codes.
///
/// A code is a contract with shipped apps: never rename or reuse one. Add a new code instead,
/// and an app that does not know it falls back to the English text.
library;

abstract final class HostdErrorCode {
  // --- command_policy.dart: verdicts -------------------------------------------------------
  /// Allowed: the workspace is marked trusted.
  static const workspaceTrusted = 'workspace_trusted';

  /// Allowed: a read-only command on the untrusted-workspace allowlist.
  static const readOnlyAllowlisted = 'read_only_allowlisted';

  /// Refused in every workspace: the command matches the hard denylist.
  static const commandHardDenied = 'command_hard_denied';

  /// Refused even in a trusted workspace: a destructive command names a path outside it.
  static const destructivePathOutsideWorkspace = 'destructive_path_outside_workspace';

  /// Refused even in a trusted workspace: `cd` out of the workspace, then a destructive command.
  static const destructiveAfterLeavingWorkspace = 'destructive_after_leaving_workspace';

  /// Refused: chaining, redirection or substitution in an untrusted workspace.
  static const untrustedShellSyntax = 'untrusted_shell_syntax';

  /// Queued: an untrusted workspace and a command that is not allowlisted.
  static const commandNeedsApproval = 'command_needs_approval';

  /// Refused: as [commandNeedsApproval], but with no approval queue to send it to.
  static const commandNotAllowlisted = 'command_not_allowlisted';

  /// Queued: a file write in an untrusted workspace.
  static const writeNeedsApproval = 'write_needs_approval';

  /// Refused in every workspace: a write under `.git/hooks`.
  static const protectedGitHooks = 'protected_git_hooks';

  /// Refused in every workspace: a write to `.git/config`.
  static const protectedGitConfig = 'protected_git_config';

  // --- containment.dart --------------------------------------------------------------------
  /// `InvalidRootError`: the path does not exist, is not a directory, or does not resolve to one.
  static const rootNotADirectory = 'root_not_a_directory';

  /// `InvalidRootError`: the path resolves to a filesystem root.
  static const rootIsFilesystemRoot = 'root_is_filesystem_root';

  /// `InvalidRootError`: the path resolves to a denylisted folder (home, system folders, ...).
  static const rootDenylisted = 'root_denylisted';

  /// `ContainmentViolation`: the path resolves outside the granted workspace root.
  static const outsideWorkspace = 'outside_workspace';

  // --- control_server.dart and the session managers behind it -------------------------------
  static const unauthorized = 'unauthorized';
  static const noSuchEndpoint = 'no_such_endpoint';
  static const internalError = 'internal_error';
  static const missingParameter = 'missing_parameter';
  static const unknownAction = 'unknown_action';
  static const rootNotGranted = 'root_not_granted';
  static const pathNotFound = 'path_not_found';
  static const fileNotUtf8 = 'file_not_utf8';
  static const approvalNotFound = 'approval_not_found';
  static const notAGitRepository = 'not_a_git_repository';
  static const gitOutputNotUtf8 = 'git_output_not_utf8';
  static const gitCommandFailed = 'git_command_failed';
  static const unknownDiagnosticsTool = 'unknown_diagnostics_tool';
  static const taskNotFound = 'task_not_found';
  static const pairingFailed = 'pairing_failed';
  static const noPairingSession = 'no_pairing_session';
  static const pairingEnded = 'pairing_ended';
  static const workspaceNotTrusted = 'workspace_not_trusted';
  static const terminalStartFailed = 'terminal_start_failed';
  static const debugStartFailed = 'debug_start_failed';
  static const debugUnreachable = 'debug_unreachable';

  /// Every code above, for tests that check a code is one of ours.
  static const Set<String> all = {
    workspaceTrusted,
    readOnlyAllowlisted,
    commandHardDenied,
    destructivePathOutsideWorkspace,
    destructiveAfterLeavingWorkspace,
    untrustedShellSyntax,
    commandNeedsApproval,
    commandNotAllowlisted,
    writeNeedsApproval,
    protectedGitHooks,
    protectedGitConfig,
    rootNotADirectory,
    rootIsFilesystemRoot,
    rootDenylisted,
    outsideWorkspace,
    unauthorized,
    noSuchEndpoint,
    internalError,
    missingParameter,
    unknownAction,
    rootNotGranted,
    pathNotFound,
    fileNotUtf8,
    approvalNotFound,
    notAGitRepository,
    gitOutputNotUtf8,
    gitCommandFailed,
    unknownDiagnosticsTool,
    taskNotFound,
    pairingFailed,
    noPairingSession,
    pairingEnded,
    workspaceNotTrusted,
    terminalStartFailed,
    debugStartFailed,
    debugUnreachable,
  };
}
