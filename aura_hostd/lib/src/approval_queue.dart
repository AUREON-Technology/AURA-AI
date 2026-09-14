/// Interactive consent queue (D5, `docs/architecture/015-desktop-ide.md`): holds operations
/// [command_policy.dart]'s `evaluateCommand`/`evaluateWrite` returned `needsApproval` for, until a
/// human answers through the Desktop IDE's approval UI (or the request times out). Originally
/// command-only (D5); generalized in D6 to also cover file writes (`OperationKind.write`), since a
/// write needing interactive consent is the same "queue it, wait, revalidate before acting" shape
/// as a command, just with a diff instead of a command string as the thing a human reviews.
///
/// A [PendingApproval] binds itself to the exact operation it was requested for via
/// [PendingApproval.operationHash] -- [TaskHandler.run]/[TaskHandler.writeFile] re-derive that same
/// hash from the current call context immediately before acting on an approved operation and
/// refuse to proceed on a mismatch, so a decision made after however long a human takes to respond
/// is checked against the state of the world at execution time, not just trusted by id. See
/// ADR-011 for why this daemon (not the server) is where consent lives at all.
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

/// What kind of operation a [PendingApproval] represents -- determines which fields are populated
/// and how the approval UI renders it (a command string vs. a diff).
enum OperationKind { command, write }

/// One file this approval would write if approved -- [finalContent] is already fully resolved
/// (a find/replace op's replacement already applied), since [PendingApproval.operationHash] and
/// the eventual write both need the exact bytes that will land on disk, not the edit instructions
/// that produced them.
class WriteOp {
  const WriteOp({required this.path, required this.finalContent});
  final String path;
  final String finalContent;
}

/// How long a pending approval waits for a human before resolving itself to [ApprovalStatus.expired].
/// A starting value, not load-bearing -- easy to tune once real usage shows whether 5 minutes
/// feels too short (a human away from the keyboard) or too long (an agent stalled needlessly).
const approvalTimeout = Duration(minutes: 5);

class PendingApproval {
  PendingApproval._({
    required this.id,
    required this.kind,
    this.canonicalCommand,
    this.displayCommand,
    this.writeOperations,
    this.diffPreview,
    required this.cwd,
    required this.rootPath,
    required this.requestedAt,
  })  : expiresAt = requestedAt.add(approvalTimeout),
        operationHash = kind == OperationKind.command
            ? PendingApproval.hashForCommand(command: canonicalCommand!, cwd: cwd, rootPath: rootPath)
            : PendingApproval.hashForWrite(operations: writeOperations!, cwd: cwd, rootPath: rootPath);

  factory PendingApproval.command({
    required String id,
    required String canonicalCommand,
    required String displayCommand,
    required String cwd,
    required String rootPath,
    required DateTime requestedAt,
  }) =>
      PendingApproval._(
        id: id,
        kind: OperationKind.command,
        canonicalCommand: canonicalCommand,
        displayCommand: displayCommand,
        cwd: cwd,
        rootPath: rootPath,
        requestedAt: requestedAt,
      );

  factory PendingApproval.write({
    required String id,
    required List<WriteOp> operations,
    required String diffPreview,
    required String cwd,
    required String rootPath,
    required DateTime requestedAt,
  }) =>
      PendingApproval._(
        id: id,
        kind: OperationKind.write,
        writeOperations: operations,
        diffPreview: diffPreview,
        cwd: cwd,
        rootPath: rootPath,
        requestedAt: requestedAt,
      );

  final String id;
  final OperationKind kind;

  /// The real command that will execute on approval -- never sent to the client. Only set when
  /// [kind] is [OperationKind.command]. See [displayCommand].
  final String? canonicalCommand;

  /// A redacted-for-display copy of [canonicalCommand] -- what the approval UI actually shows a
  /// human, so an incidental credential in the command text isn't surfaced verbatim. Execution
  /// always uses [canonicalCommand]; this field exists purely for the `GET /approvals` response.
  final String? displayCommand;

  /// The files this approval would write, with fully-resolved final content -- only set when
  /// [kind] is [OperationKind.write].
  final List<WriteOp>? writeOperations;

  /// A redacted unified diff of [writeOperations] against their current on-disk content -- what
  /// the approval UI shows a human for a write, mirroring [displayCommand]'s role for a command.
  /// Only set when [kind] is [OperationKind.write].
  final String? diffPreview;

  final String cwd;
  final String rootPath;
  final DateTime requestedAt;
  final DateTime expiresAt;

  /// Binds this approval to the exact operation/cwd/root it was requested for -- computed once at
  /// creation. [TaskHandler.run]/[TaskHandler.writeFile] recompute this same hash from the current
  /// call context right before acting and refuse to proceed on a mismatch (root revoked, target
  /// file changed on disk, or any other change between request and approval). Renamed from D5's
  /// `commandHash`: the concept was already "the operation this approval is for," just command-only
  /// until D6 added a second kind.
  final String operationHash;

  ApprovalStatus status = ApprovalStatus.pending;

  final Completer<ApprovalStatus> _decision = Completer<ApprovalStatus>();

  /// Resolves once a human responds, this approval expires, or it is cancelled -- never resolves
  /// to [ApprovalStatus.pending].
  Future<ApprovalStatus> get decision => _decision.future;

  Map<String, Object?> toJson() => {
        'id': id,
        'kind': kind.name,
        'command': kind == OperationKind.command ? displayCommand : null,
        'diff_preview': kind == OperationKind.write ? diffPreview : null,
        'paths': kind == OperationKind.write ? writeOperations!.map((o) => o.path).toList() : null,
        'cwd': cwd,
        'root': rootPath,
        'requested_at': requestedAt.toIso8601String(),
        'expires_at': expiresAt.toIso8601String(),
        'status': status.name,
      };

  /// Public so [TaskHandler.run] can recompute the same hash from the current call context right
  /// before executing an approved command, and compare it against [operationHash] (D5 plan design
  /// decision 5: revalidate, don't just trust the id).
  static String hashForCommand({required String command, required String cwd, required String rootPath}) {
    final bytes = utf8.encode('$command|$cwd|$rootPath');
    return sha256.convert(bytes).toString();
  }

  /// Public so [TaskHandler.writeFile]/`writeFiles` can recompute the same hash from current disk
  /// state right before writing an approved operation, and compare it against [operationHash] (D6
  /// plan section C.3: a write has an extra staleness risk a command doesn't -- the target file(s)
  /// can themselves have changed between request and approval, not just the root/trust -- so this
  /// must be re-derived from *current* file content, not merely re-checked against the root).
  /// Hashes content digests rather than raw content so a large write's approval object stays small
  /// and this recomputation stays cheap even for many/large files.
  static String hashForWrite({
    required List<WriteOp> operations,
    required String cwd,
    required String rootPath,
  }) {
    final perFile = operations
        .map((o) => '${o.path}|${sha256.convert(utf8.encode(o.finalContent)).toString().substring(0, 16)}')
        .join(';');
    final bytes = utf8.encode('$perFile|$cwd|$rootPath');
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

/// Same best-effort credential redaction as [redactForDisplay], applied line-by-line to a unified
/// diff's `+`/context lines before it's ever displayed or persisted in a [PendingApproval] (D6
/// plan decision 6) -- a file diff is at least as likely as a shell command to carry a secret
/// verbatim (an edited `.env`, a hardcoded credential a developer just typed in).
String redactDiffForDisplay(String diffText) {
  return diffText.split('\n').map(redactForDisplay).join('\n');
}

class ApprovalQueue {
  final Map<String, PendingApproval> _pending = {};

  /// Creates and registers a new pending command approval. [displayCommand] is derived via
  /// [redactForDisplay] here, once, rather than at read time.
  PendingApproval createForCommand({
    required String canonicalCommand,
    required String cwd,
    required String rootPath,
  }) {
    final approval = PendingApproval.command(
      id: _nextId(),
      canonicalCommand: canonicalCommand,
      displayCommand: redactForDisplay(canonicalCommand),
      cwd: cwd,
      rootPath: rootPath,
      requestedAt: DateTime.now(),
    );
    _pending[approval.id] = approval;
    return approval;
  }

  /// Creates and registers a new pending write approval. [diffText] is redacted via
  /// [redactDiffForDisplay] here, once, rather than at read time -- [operations] themselves are
  /// never redacted (they hold the exact bytes that will be written on approval).
  PendingApproval createForWrite({
    required List<WriteOp> operations,
    required String diffText,
    required String cwd,
    required String rootPath,
  }) {
    final approval = PendingApproval.write(
      id: _nextId(),
      operations: operations,
      diffPreview: redactDiffForDisplay(diffText),
      cwd: cwd,
      rootPath: rootPath,
      requestedAt: DateTime.now(),
    );
    _pending[approval.id] = approval;
    return approval;
  }

  String _nextId() => 'appr_${DateTime.now().microsecondsSinceEpoch}_${_pending.length}';

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
