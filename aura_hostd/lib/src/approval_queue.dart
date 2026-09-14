/// Interactive consent queue (D5, `docs/architecture/015-desktop-ide.md`): holds commands
/// [command_policy.dart]'s `evaluateCommand` returned `needsApproval` for, until a human answers
/// through the Desktop IDE's approval UI (or the request times out).
///
/// A [PendingApproval] binds itself to the exact operation it was requested for via
/// [PendingApproval.commandHash] -- [TaskHandler.run] re-derives that same hash from the current
/// call context immediately before executing an approved command and refuses to run on a
/// mismatch, so a decision made after however long a human takes to respond is checked against
/// the state of the world at execution time, not just trusted by id. See ADR-011 for why this
/// daemon (not the server) is where consent lives at all.
///
/// Identity is anchored on [PendingApproval.rootPath], not a "workspace id" -- this daemon has no
/// such concept anywhere (grant/revoke/trust all key on the granted root's canonical path; see
/// `containment.dart`). This matches `terminal_session_manager.dart`'s existing precedent
/// (`handleConnection({required String rootPath})`): the client, not the daemon, maps a root path
/// to its own `workspaceId` for `IdeEvent` purposes.
library;

import 'dart:async';
import 'dart:convert';

import 'package:crypto/crypto.dart';

enum ApprovalStatus { pending, approved, rejected, expired, cancelled }

/// How long a pending approval waits for a human before resolving itself to [ApprovalStatus.expired].
/// A starting value, not load-bearing -- easy to tune once real usage shows whether 5 minutes
/// feels too short (a human away from the keyboard) or too long (an agent stalled needlessly).
const approvalTimeout = Duration(minutes: 5);

class PendingApproval {
  PendingApproval({
    required this.id,
    required this.canonicalCommand,
    required this.displayCommand,
    required this.cwd,
    required this.rootPath,
    required this.requestedAt,
  })  : expiresAt = requestedAt.add(approvalTimeout),
        commandHash = _hashOf(command: canonicalCommand, cwd: cwd, rootPath: rootPath);

  final String id;

  /// The real command that will execute on approval -- never sent to the client. See
  /// [displayCommand].
  final String canonicalCommand;

  /// A redacted-for-display copy of [canonicalCommand] -- what the approval UI actually shows a
  /// human, so an incidental credential in the command text isn't surfaced verbatim. Execution
  /// always uses [canonicalCommand]; this field exists purely for the `GET /approvals` response.
  final String displayCommand;

  final String cwd;
  final String rootPath;
  final DateTime requestedAt;
  final DateTime expiresAt;

  /// Binds this approval to the exact command/cwd/root it was requested for -- computed once at
  /// creation from [canonicalCommand]. [TaskHandler.run] recomputes this same hash from the
  /// current call context right before executing and refuses to run on a mismatch (root revoked,
  /// or any other change between request and approval).
  final String commandHash;

  ApprovalStatus status = ApprovalStatus.pending;

  final Completer<ApprovalStatus> _decision = Completer<ApprovalStatus>();

  /// Resolves once a human responds, this approval expires, or it is cancelled -- never resolves
  /// to [ApprovalStatus.pending].
  Future<ApprovalStatus> get decision => _decision.future;

  Map<String, Object?> toJson() => {
        'id': id,
        'command': displayCommand,
        'cwd': cwd,
        'root': rootPath,
        'requested_at': requestedAt.toIso8601String(),
        'expires_at': expiresAt.toIso8601String(),
        'status': status.name,
      };

  /// Public so [TaskHandler.run] can recompute the same hash from the current call context right
  /// before executing an approved command, and compare it against [commandHash] (design decision
  /// 5 of D5's plan: revalidate, don't just trust the id).
  static String hashFor({required String command, required String cwd, required String rootPath}) =>
      _hashOf(command: command, cwd: cwd, rootPath: rootPath);

  static String _hashOf({required String command, required String cwd, required String rootPath}) {
    final bytes = utf8.encode('$command|$cwd|$rootPath');
    return sha256.convert(bytes).toString();
  }
}

/// Redacts common credential-shaped substrings from a command string before it is ever shown to a
/// human -- best-effort only, not a security boundary (the real command still executes with real
/// secrets; see [PendingApproval.displayCommand]'s doc comment). Matches the spirit of
/// `task_handler.dart::_effectiveShellCommand`'s existing string handling without touching that
/// function.
String redactForDisplay(String command) {
  var result = command;
  result = result.replaceAllMapped(
    RegExp(r'(Bearer)\s+\S+', caseSensitive: false),
    (m) => '${m[1]} [REDACTED]',
  );
  result = result.replaceAllMapped(
    RegExp(r'(Authorization\s*[:=]\s*)\S+', caseSensitive: false),
    (m) => '${m[1]}[REDACTED]',
  );
  result = result.replaceAllMapped(
    RegExp(r'''((?:api[_-]?key|token|password|secret)\s*[:=]\s*)("[^"]*"|'[^']*'|\S+)''', caseSensitive: false),
    (m) => '${m[1]}[REDACTED]',
  );
  return result;
}

class ApprovalQueue {
  final Map<String, PendingApproval> _pending = {};

  /// Creates and registers a new pending approval for [canonicalCommand]. [displayCommand] is
  /// derived via [redactForDisplay] here, once, rather than at read time.
  PendingApproval create({
    required String canonicalCommand,
    required String cwd,
    required String rootPath,
  }) {
    final approval = PendingApproval(
      id: 'appr_${DateTime.now().microsecondsSinceEpoch}_${_pending.length}',
      canonicalCommand: canonicalCommand,
      displayCommand: redactForDisplay(canonicalCommand),
      cwd: cwd,
      rootPath: rootPath,
      requestedAt: DateTime.now(),
    );
    _pending[approval.id] = approval;
    return approval;
  }

  List<PendingApproval> list() =>
      _pending.values.where((a) => a.status == ApprovalStatus.pending).toList(growable: false);

  /// Records a human's response. Idempotent: responding again to an id that has already resolved
  /// (approved, rejected, expired, or cancelled) is a no-op, not an error -- it never overwrites a
  /// prior decision. Returns `false` only when [id] was never a known approval at all, so the
  /// caller can respond `404`.
  bool respond(String id, bool approved) {
    final approval = _pending[id];
    if (approval == null) return false;
    if (approval.status != ApprovalStatus.pending) return true;
    approval.status = approved ? ApprovalStatus.approved : ApprovalStatus.rejected;
    approval._decision.complete(approval.status);
    return true;
  }

  /// Waits for [approval] to be answered, or for [approvalTimeout] to elapse -- whichever comes
  /// first. On timeout, marks the approval [ApprovalStatus.expired] itself (rather than leaving it
  /// `pending` forever) so a subsequent `GET /approvals` stops listing it and a late human response
  /// finds it already resolved (and is safely ignored by [respond]'s idempotency).
  Future<ApprovalStatus> awaitDecision(PendingApproval approval) async {
    return approval.decision.timeout(
      approvalTimeout,
      onTimeout: () {
        if (approval.status == ApprovalStatus.pending) {
          approval.status = ApprovalStatus.expired;
          approval._decision.complete(ApprovalStatus.expired);
        }
        return approval.status;
      },
    );
  }
}
