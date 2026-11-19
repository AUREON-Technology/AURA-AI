import 'dart:async';
import 'dart:convert';
import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/sync/remote_deletes.dart';
import 'package:aura_client/core/sync/sync_payloads.dart';
import 'package:aura_client/core/undo/undoable_deletes.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'package:aura_client/core/ai/engineer/engineer_consent_repository.dart';
import 'agent_config_repository.dart';
import 'chat_connection_error.dart';
import 'turn_meta.dart';

// `interrupted`: the stream ended early (Stop, a drop, or the idle timeout) and whatever text had
// arrived was kept, marked [TurnMeta.interrupted] -- never replaced by the error bubble.
/// `unavailable`: the backend refused the turn's content class (`capability_unavailable`, the
/// chat-only launch). Not a connection error, so it gets plain copy and no Retry card.
/// `consentRequired` / `quotaExhausted`: the Engineer refusals of D-03 and D-05
/// (`consent_required`, `user_quota_exhausted`, both `retryable: false`). Retrying reaches the
/// same refusal, so neither gets a Retry card: the first asks for consent, the second says when
/// the daily limit resets.
enum _SseSendOutcome {
  succeeded,
  failed,
  interrupted,
  unavailable,
  withheld,
  blocked,
  consentRequired,
  quotaExhausted,
  approvalExpired,
  planLimit,
}

class _SseResult {
  final _SseSendOutcome outcome;
  final String? message;
  final Object? details;
  const _SseResult(this.outcome, [this.message, this.details]);
}

/// The outcome for a refusal the server marks as needing something other than a retry, or null.
_SseSendOutcome? _engineerRefusal(String? errorKind) => switch (errorKind) {
      'consent_required' => _SseSendOutcome.consentRequired,
      'user_quota_exhausted' => _SseSendOutcome.quotaExhausted,
      // G-14b: an approval id the server no longer holds (expired after its TTL, already used,
      // or unknown). Retrying would resend the original message as a brand-new turn.
      'confirmation_invalid' => _SseSendOutcome.approvalExpired,
      _ => null,
    };

/// How long the server keeps a held turn's approval ids (its confirmation TTL): a held call older
/// than this cannot be approved any more, so it is not offered again after a restart.
const kHeldTurnTtl = Duration(hours: 1);

class PendingActions extends Notifier<List<dynamic>> {
  @override
  List<dynamic> build() => [];
  
  void setActions(List<dynamic> actions) => state = actions;
  void clear() => state = [];
}

final pendingActionsProvider = NotifierProvider<PendingActions, List<dynamic>>(() {
  return PendingActions();
});

class PendingConfirmation extends Notifier<List<PendingToolCall>> {
  @override
  List<PendingToolCall> build() => [];

  void setPending(List<PendingToolCall> calls) => state = calls;

  /// Adds to the existing pending list rather than replacing it, so a second turn's pending
  /// calls don't discard a still-unresolved earlier turn's -- each call carries its own [turnId]
  /// (Phase 2's per-message confirmation card filters by it).
  void addPending(List<PendingToolCall> calls) => state = [...state, ...calls];

  void removeByCallId(String callId) =>
      state = state.where((c) => c.callId != callId).toList();

  /// Call ids are positional per turn (`step_s1` recurs in every turn); an approval id is
  /// unique, so this removes exactly one call.
  void removeByApprovalId(String approvalId) =>
      state = state.where((c) => c.approvalId != approvalId).toList();

  void removeByTurnId(String turnId) =>
      state = state.where((c) => c.turnId != turnId).toList();

  void clear() => state = [];
}

final pendingConfirmationProvider =
    NotifierProvider<PendingConfirmation, List<PendingToolCall>>(PendingConfirmation.new);

/// The id of the assistant message currently receiving a stream, or null
/// when nothing is in flight. Distinct from "content is empty" -- lets the
/// UI show a loading skeleton only while a turn is genuinely still
/// streaming, not forever on a message that legitimately finished empty.
class StreamingMessageId extends Notifier<String?> {
  @override
  String? build() => null;

  void set(String? messageId) => state = messageId;
}

final streamingMessageIdProvider = NotifierProvider<StreamingMessageId, String?>(() {
  return StreamingMessageId();
});

class ChatRepository {
  final AppDatabase _db;
  final Ref _ref;
  final _uuid = const Uuid();

  // The in-flight turn's event stream, kept so [stopStreaming] can cancel it, and the assistant
  // message that Stop was pressed for.
  StreamIterator<SseChatEvent>? _activeStream;
  /// FSA-006: the workspace root each conversation last sent, so a retry (`retryLastFailedTurn`) or an
  /// approval resend (`confirmPendingCalls`) keeps running in the root the user was looking at
  /// rather than falling back to the daemon's first granted root.
  final Map<String, String> _lastWorkspaceRoot = {};
  /// WP4: the conversations whose turns asked for an isolated worktree, kept for the same reason.
  final Set<String> _isolatedConversations = {};
  String? _stoppedMessageId;
  /// A send that has passed the in-flight gate but has not marked its reply as streaming yet
  /// (it is still saving its rows). Without it, two sends started in the same frame -- a double
  /// tap on Retry -- would both pass the gate and both reach the server.
  bool _claimingSend = false;
  /// A retry between its gate and its send, for the same reason (F2).
  bool _retrying = false;

  ChatRepository(this._db, this._ref);

  /// Stops the in-flight response (the composer's Stop button). The chat unlocks at once -- the
  /// streaming id is cleared here, not when the network notices -- and the text received so far
  /// stays in the bubble, marked interrupted, when [sendChatMessage] finishes the turn.
  void stopStreaming() {
    final streamingId = _ref.read(streamingMessageIdProvider);
    if (streamingId == null) return;
    _stoppedMessageId = streamingId;
    _ref.read(streamingMessageIdProvider.notifier).set(null);
    // A pending `moveNext` completes with false on cancel, so the turn loop ends right away.
    final active = _activeStream;
    if (active != null) unawaited(active.cancel().catchError((_) {}));
  }

  // Watch messages for a specific conversation session.
  //
  // `limit`, when given, bounds the query to the most recent `limit` messages instead of the
  // whole conversation. Each row's encrypted columns (content, citations, tool calls, turn meta)
  // are decrypted synchronously on this isolate by Drift's `EncryptedTextConverter` when the
  // query result is mapped -- an unbounded history load on a long conversation means hundreds of
  // back-to-back AES-GCM calls on the UI isolate right as the chat screen builds, which is real,
  // measurable jank (most visible in debug builds, where the pure-Dart cipher isn't JIT-warmed).
  // Ordering by `createdAt DESC` to take the most recent `limit` rows, then reversing back to
  // ascending order for display, keeps this a single indexed query rather than a full scan.
  Stream<List<Message>> watchMessages(String conversationId, {int? limit}) {
    final query = _db.select(_db.messages)
      ..where((m) => m.conversationId.equals(conversationId));
    if (limit == null) {
      query.orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.asc)]);
      return query.watch();
    }
    query
      ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.desc)])
      ..limit(limit);
    return query.watch().map((rows) => rows.reversed.toList());
  }

  // Watch all active conversations (grouped by conversationId, ordering by latest)
  Stream<List<String>> watchConversations() {
    final query = _db.selectOnly(_db.messages)
      ..addColumns([_db.messages.conversationId])
      ..groupBy([_db.messages.conversationId]);

    return query.map((row) => row.read(_db.messages.conversationId)!).watch();
  }

  /// Same as [watchConversations], scoped to conversations that have at least one assistant
  /// message tagged with [agentId] -- `agentId` is only ever set on the assistant placeholder row
  /// (`sendChatMessage` below), never the user's own message, so filtering on it is what makes
  /// this "conversations involving this persona" rather than "every conversation in the app."
  /// Used by the Desktop IDE's `AgentPanel` (D6/D7 Part B.D1) to show only Engineer-persona
  /// sessions as switchable "tasks," not the mobile Copilot's sessions for other personas.
  Stream<List<String>> watchConversationsForAgent(String agentId) {
    final query = _db.selectOnly(_db.messages, distinct: true)
      ..addColumns([_db.messages.conversationId])
      ..where(_db.messages.agentId.equals(agentId));

    return query.map((row) => row.read(_db.messages.conversationId)!).watch();
  }

  Future<void> _enqueueUpsert(String id) async {
    final row = await (_db.select(_db.messages)..where((m) => m.id.equals(id))).getSingleOrNull();
    if (row == null) return;
    await _db.enqueueOutbox(
      table: 'messages',
      recordId: id,
      operation: 'upsert',
      payload: SyncPayloads.message(row),
    );
  }

  Future<void> _enqueueDelete(String id) async {
    await _db.enqueueOutbox(table: 'messages', recordId: id, operation: 'delete');
  }

  /// The id of [conversationId]'s newest assistant message, or null when it has none. A failed
  /// turn offers Retry only while it is the newest (F2): an older one has been followed by
  /// other turns, and resending it now would answer out of order.
  Stream<String?> watchLatestAssistantId(String conversationId) {
    final query = _db.select(_db.messages)
      ..where((m) => m.conversationId.equals(conversationId) & m.role.equals('assistant'))
      ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.desc)])
      ..limit(1);
    return query.watch().map((rows) => rows.isEmpty ? null : rows.first.id);
  }

  /// Sends a message and streams the response, saving to Drift as it arrives.
  ///
  /// Returns false when the send was ignored because another reply is still in flight, and
  /// otherwise whether the turn got an answer: false too when it ended as a connection failure
  /// ([kChatConnectionErrorPrefix], the bubble with Retry). [onAccepted] runs once the send has
  /// passed the in-flight gate and saved its rows, before the request goes out.
  Future<bool> sendChatMessage({
    required String conversationId,
    required String userContent,
    bool useRag = true,
    bool useMemory = true,
    bool useTools = true,
    List<String> approvalIds = const [],
    bool persistUserMessage = true,
    List<ChatImageAttachment> images = const [],
    // The Desktop IDE's active workspace root (FSA-006), or null from any surface without one.
    // Remembered per conversation so a retry of this message runs its commands in the same place.
    String? workspaceRoot,
    // WP4 (D-09): work in the conversation's own git worktree. Remembered like [workspaceRoot].
    bool desktopTaskIsolation = false,
    // G-14c: the persona to ask and to label the reply with. Null means the active persona; an
    // approval resend passes the held turn's own, which the user may have switched away from.
    String? agentId,
    Future<void> Function()? onAccepted,
  }) async {
    if (_claimingSend || _ref.read(streamingMessageIdProvider) != null) {
      debugPrint("[ChatRepository] ignoring send while a stream is already in flight");
      return false;
    }
    _claimingSend = true;
    if (workspaceRoot != null) {
      _lastWorkspaceRoot[conversationId] = workspaceRoot;
    }
    if (desktopTaskIsolation) _isolatedConversations.add(conversationId);
    final effectiveRoot = workspaceRoot ?? _lastWorkspaceRoot[conversationId];
    final effectiveIsolation = desktopTaskIsolation || _isolatedConversations.contains(conversationId);
    final String assistantMsgId;
    final String activeAgentId;

    try {
      _ref.read(pendingActionsProvider.notifier).clear();
      // Pending confirmations are turn-scoped (each `PendingToolCall.turnId`), not cleared wholesale
      // here -- a new message must not silently discard a still-unresolved earlier turn's pending
      // calls. `confirmPendingCalls`/`rejectPendingCalls` remove entries as they resolve.

      if (persistUserMessage) {
        final userMsgId = _uuid.v4();
        // Before the insert, so the bubble's first frame already has its thumbnails.
        _ref.read(sentChatImagesProvider.notifier).remember(userMsgId, images);
        await _db.into(_db.messages).insert(
          MessagesCompanion.insert(
            id: userMsgId,
            conversationId: conversationId,
            role: "user",
            // The bytes stay in memory for this one request (ADR-031); the transcript keeps the
            // same marker the server's session history does.
            content: images.isEmpty
                ? userContent
                : '$userContent\n${imagesAttachedNote(images.length)}',
            createdAt: DateTime.now(),
            updatedAt: Value(DateTime.now()),
          ),
        );
        await _enqueueUpsert(userMsgId);
      }

      // 2. Save Placeholder Assistant Message
      assistantMsgId = _uuid.v4();
      activeAgentId = agentId ?? _ref.read(activeAgentIdProvider);
      await _db.into(_db.messages).insert(
        MessagesCompanion.insert(
          id: assistantMsgId,
          conversationId: conversationId,
          role: "assistant",
          content: "",
          modelUsed: Value(activeAgentId),
          agentId: Value(activeAgentId),
          // Where the turn runs, from the start: a turn that fails before `done` is retried in the
          // same folder and worktree, after a restart too (F2).
          turnMetaJson: effectiveRoot != null || effectiveIsolation
              ? Value(TurnMeta(workspaceRoot: effectiveRoot, desktopTaskIsolation: effectiveIsolation).encode())
              : const Value.absent(),
          createdAt: DateTime.now(),
          updatedAt: Value(DateTime.now()),
        ),
      );
      await _enqueueUpsert(assistantMsgId);

      // 3. Stream against the v3 kernel. Conversation history is loaded server-side
      // from episodic memory keyed by session_id. SSE errors stay a retry bubble;
      // there is no v1 orchestrator fallback.
      _ref.read(streamingMessageIdProvider.notifier).set(assistantMsgId);
    } finally {
      _claimingSend = false;
    }
    EngineerConsentPrompt? consentPrompt;
    var answered = true;
    try {
      if (onAccepted != null) {
        try {
          await onAccepted();
        } catch (e) {
          debugPrint("[ChatRepository] after-accept step failed: $e");
        }
      }
      final sseOutcome = await _sendViaSse(
        message: userContent,
        sessionId: conversationId,
        agentId: activeAgentId,
        useRag: useRag,
        useMemory: useMemory,
        useTools: useTools,
        approvalIds: approvalIds,
        assistantMsgId: assistantMsgId,
        images: images,
        workspaceRoot: effectiveRoot,
        desktopTaskIsolation: effectiveIsolation,
      );

      if (sseOutcome.outcome == _SseSendOutcome.interrupted) {
        // Partial text, already saved and marked by `_sendViaSse`.
      } else if (sseOutcome.outcome == _SseSendOutcome.withheld) {
        await _updateMessageText(assistantMsgId, kChatWithheldText);
      } else if (sseOutcome.outcome == _SseSendOutcome.blocked) {
        await _updateMessageText(assistantMsgId, kChatBlockedText);
      } else if (sseOutcome.outcome == _SseSendOutcome.planLimit) {
        await _updateMessageText(assistantMsgId, sseOutcome.message!);
      } else if (sseOutcome.outcome == _SseSendOutcome.unavailable) {
        // Deliberately without `kChatConnectionErrorPrefix`: that prefix is what puts a Retry
        // card on the row, and retrying this turn reaches the same refusal.
        await _updateMessageText(assistantMsgId, kChatFeatureUnavailableText);
      } else if (sseOutcome.outcome == _SseSendOutcome.consentRequired) {
        // Consent was withdrawn elsewhere, or the notice changed: what this app knew is stale.
        await _updateMessageText(assistantMsgId, kChatEngineerConsentNeededText);
        _ref.invalidate(engineerConsentProvider);
        final details = sseOutcome.details;
        final current = details is Map ? details['current_version'] : null;
        consentPrompt = EngineerConsentPrompt(
          conversationId: conversationId,
          messageId: assistantMsgId,
          currentVersion: current is String ? current : null,
        );
      } else if (sseOutcome.outcome == _SseSendOutcome.quotaExhausted) {
        await _updateMessageText(assistantMsgId, engineerLimitReachedText(sseOutcome.details));
      } else if (sseOutcome.outcome == _SseSendOutcome.approvalExpired) {
        await _updateMessageText(assistantMsgId, kChatApprovalExpiredText);
      } else if (sseOutcome.message != null) {
        answered = false;
        await _updateMessageText(
          assistantMsgId,
          '$kChatConnectionErrorPrefix: ${sseOutcome.message}',
        );
      } else if (sseOutcome.outcome == _SseSendOutcome.failed) {
        answered = false;
        await _updateMessageText(assistantMsgId, kChatConnectionErrorMessage);
      }
    } finally {
      if (_stoppedMessageId == assistantMsgId) _stoppedMessageId = null;
      if (_ref.read(streamingMessageIdProvider) == assistantMsgId) {
        _ref.read(streamingMessageIdProvider.notifier).set(null);
      }
    }
    // After the stream is released: agreeing in the sheet resends this turn, and a send is
    // ignored while a stream is still marked in flight.
    if (consentPrompt != null) {
      _ref.read(engineerConsentPromptProvider.notifier).request(consentPrompt);
    }
    return answered;
  }

  /// Re-sends the turn of the assistant message [errorMessageId] -- a connection-error bubble,
  /// an empty answer, or a turn that asked for Engineer consent -- with that turn's own user
  /// message, persona, workspace root and isolation (F2 / INT-108). It used to resend whichever
  /// user message was last, whatever bubble was tapped, and in the active persona.
  ///
  /// Returns false, changing nothing, while another reply is in flight or when the turn is no
  /// longer in the conversation. The failed rows are deleted only once the resend has been
  /// accepted, so a refused retry cannot lose the message. An approval resend that failed has
  /// no user message of its own (it resumed a held turn): its retry asks again as a new turn,
  /// without the approval, and leaves the held turn's message where it is.
  Future<bool> retryLastFailedTurn({
    required String conversationId,
    required String errorMessageId,
  }) async {
    if (_retrying || _ref.read(streamingMessageIdProvider) != null) return false;
    _retrying = true;
    try {
      final turn = await _turnOf(conversationId, (m) => m.id == errorMessageId);
      if (turn == null) return false;
      // Again after the await: a reply may have started meanwhile.
      if (_ref.read(streamingMessageIdProvider) != null) return false;

      final ownUser = turn.ownUserMessage;
      final meta = TurnMeta.decode(turn.assistant.turnMetaJson);
      // The stored text carries the image marker, not the images (ADR-031): resend the text
      // alone, with the images themselves while this session still holds them.
      final images = ownUser == null
          ? const <ChatImageAttachment>[]
          : _ref.read(sentChatImagesProvider)[ownUser.id] ?? const <ChatImageAttachment>[];
      var accepted = false;
      await sendChatMessage(
        conversationId: conversationId,
        userContent: turn.userContent,
        images: images,
        persistUserMessage: ownUser != null,
        agentId: turn.assistant.agentId,
        workspaceRoot: meta.workspaceRoot,
        desktopTaskIsolation: meta.desktopTaskIsolation,
        onAccepted: () async {
          accepted = true;
          await (_db.delete(_db.messages)..where((m) => m.id.equals(errorMessageId))).go();
          await _enqueueDelete(errorMessageId);
          if (ownUser != null) {
            await (_db.delete(_db.messages)..where((m) => m.id.equals(ownUser.id))).go();
            await _enqueueDelete(ownUser.id);
          }
        },
      );
      return accepted;
    } finally {
      _retrying = false;
    }
  }

  /// Approves [calls] -- held calls from ONE turn -- by their server-issued approval ids (F-04).
  ///
  /// The approval references the pending calls themselves, never "the latest message": the
  /// backend resumes the turn it stored when it asked, and runs only calls identical to the
  /// approved ones. The resend carries the user message of the bubble's own turn, so a later
  /// message in the conversation can neither be replayed nor consume the approval.
  ///
  /// Returns false, changing nothing, while another stream is in flight (the send would be
  /// dropped and the approval silently lost) or when [calls] have no approval id or span turns.
  /// Pending entries are removed only once the server has answered the resend: one that ended
  /// as a connection failure leaves the card up, so the approval can be given again.
  Future<bool> confirmPendingCalls({
    required String conversationId,
    required List<PendingToolCall> calls,
  }) async {
    if (_ref.read(streamingMessageIdProvider) != null) return false;
    final approvable = calls.where((c) => c.canConfirm && c.turnId != null).toList();
    if (approvable.isEmpty) return false;
    final turnId = approvable.first.turnId!;
    if (approvable.any((c) => c.turnId != turnId)) return false;

    final held = await _heldTurn(conversationId, turnId);
    if (held == null) return false;
    // Again after the await: `sendChatMessage` drops a send while a stream is in flight, and
    // the entries below must not be removed for a send that never happened.
    if (_ref.read(streamingMessageIdProvider) != null) return false;

    final heldMeta = TurnMeta.decode(held.assistant.turnMetaJson);
    var accepted = false;
    final answered = await sendChatMessage(
      conversationId: conversationId,
      userContent: held.userContent,
      approvalIds: approvable.map((c) => c.approvalId!).toList(),
      persistUserMessage: false,
      // G-14c: the held turn's persona, whatever is active now.
      agentId: held.assistant.agentId,
      // Where the held turn ran -- still known after a restart, unlike the in-memory maps.
      workspaceRoot: heldMeta.workspaceRoot,
      desktopTaskIsolation: heldMeta.desktopTaskIsolation,
      onAccepted: () async => accepted = true,
    );
    if (!accepted) return false;
    // F-04: a resend that failed before the server answered left the approval unused, so the card
    // stays and the user can approve again (an id the server did spend comes back as "expired").
    if (!answered) return true;
    // The server consumed that whole held turn: the approved calls ran, and anything still
    // needing approval came back under the new turn with fresh ids. The old entries are dead.
    _ref.read(pendingConfirmationProvider.notifier).removeByTurnId(turnId);
    await _resolveHeldCalls(held.assistant, heldMeta.pendingToolCalls.map((c) => c.approvalId));
    return true;
  }

  /// G-14a: offers again the held calls of [conversationId] that were neither approved nor
  /// declined and are still inside the server's TTL ([kHeldTurnTtl], from the turn), so an app
  /// restart does not lose a confirmation the server is still waiting for. Idempotent.
  Future<void> rehydratePendingConfirmations(String conversationId, {DateTime? now}) async {
    final cutoff = (now ?? DateTime.now()).subtract(kHeldTurnTtl);
    final rows = await (_db.select(_db.messages)
          ..where((m) => m.conversationId.equals(conversationId) & m.role.equals('assistant'))
          ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.asc)]))
        .get();
    final notifier = _ref.read(pendingConfirmationProvider.notifier);
    final known = _ref.read(pendingConfirmationProvider).map((c) => c.approvalId).toSet();
    for (final row in rows) {
      if (!row.createdAt.isAfter(cutoff)) continue;
      final meta = TurnMeta.decode(row.turnMetaJson);
      if (meta.confirmationResolved || meta.pendingToolCalls.isEmpty) continue;
      final turnId = meta.turnId ?? row.id;
      final fresh = meta.pendingToolCalls
          .where((c) => c.canConfirm && !known.contains(c.approvalId))
          .map((c) => c.copyWith(turnId: turnId))
          .toList();
      if (fresh.isNotEmpty) notifier.addPending(fresh);
    }
  }

  /// Persists that the user answered [approvalIds] of [assistant]'s held calls, so they are not
  /// offered again after a restart; the turn is resolved once none is left.
  Future<void> _resolveHeldCalls(Message assistant, Iterable<String?> approvalIds) async {
    final answered = approvalIds.whereType<String>().toSet();
    final meta = TurnMeta.decode(assistant.turnMetaJson);
    final left = meta.pendingToolCalls.where((c) => !answered.contains(c.approvalId)).toList();
    await _updateMessageTurnMeta(
      assistant.id,
      meta.copyWith(pendingToolCalls: left, confirmationResolved: left.isEmpty),
    );
  }

  /// The assistant bubble of [turnId] (its `turn_id`, or the message id standing in for one)
  /// and the user message that produced it, or null when that turn is no longer in the
  /// conversation.
  Future<({Message assistant, String userContent})?> _heldTurn(String conversationId, String turnId) async {
    // The server resumes the stored turn (F-04); its images were already understood then.
    final turn = await _turnOf(
      conversationId,
      (m) => m.id == turnId || TurnMeta.decode(m.turnMetaJson).turnId == turnId,
    );
    return turn == null ? null : (assistant: turn.assistant, userContent: turn.userContent);
  }

  /// The first assistant message of [conversationId] that [matches], the text of the user
  /// message that produced it, and that user message itself when it belongs to this turn alone
  /// -- null for an approval resend, which follows its held turn's reply rather than a message
  /// of its own. Null when no such turn is in the conversation.
  Future<({Message assistant, String userContent, Message? ownUserMessage})?> _turnOf(
    String conversationId,
    bool Function(Message assistant) matches,
  ) async {
    final messages = await (_db.select(_db.messages)
          ..where((m) => m.conversationId.equals(conversationId))
          ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.asc)]))
        .get();
    final index = messages.indexWhere((m) => m.role == 'assistant' && matches(m));
    if (index < 0) return null;
    var own = true;
    for (var i = index - 1; i >= 0; i--) {
      if (messages[i].role == 'user') {
        return (
          assistant: messages[index],
          userContent: splitImagesNote(messages[i].content).text,
          ownUserMessage: own ? messages[i] : null,
        );
      }
      if (messages[i].role == 'assistant') own = false;
    }
    return null;
  }

  /// Rejects pending calls locally -- no request is sent. The backend never receives their
  /// approval ids, so nothing runs and the held turn simply expires server-side. The answer is
  /// persisted on the held turn, so a restart does not offer the calls again (G-14a).
  Future<void> rejectPendingCalls(List<PendingToolCall> calls) async {
    final notifier = _ref.read(pendingConfirmationProvider.notifier);
    for (final call in calls) {
      if (call.approvalId != null) {
        notifier.removeByApprovalId(call.approvalId!);
      } else if (call.callId != null) {
        notifier.removeByCallId(call.callId!);
      }
    }
    for (final turnId in calls.map((c) => c.turnId).whereType<String>().toSet()) {
      final rows = await (_db.select(_db.messages)..where((m) => m.role.equals('assistant'))).get();
      for (final row in rows) {
        if (row.id == turnId || TurnMeta.decode(row.turnMetaJson).turnId == turnId) {
          await _resolveHeldCalls(row, calls.where((c) => c.turnId == turnId).map((c) => c.approvalId));
        }
      }
    }
  }

  Future<_SseResult> _sendViaSse({
    required String message,
    required String sessionId,
    required String agentId,
    required bool useRag,
    required bool useMemory,
    required bool useTools,
    List<String> approvalIds = const [],
    required String assistantMsgId,
    List<ChatImageAttachment> images = const [],
    String? workspaceRoot,
    bool desktopTaskIsolation = false,
  }) async {
    final stopwatch = Stopwatch()..start();
    String displayText = "";
    DateTime lastDbUpdate = DateTime.now();
    final citations = <String>[];
    final toolCalls = <Map<String, dynamic>>[];
    var meta = TurnMeta(workspaceRoot: workspaceRoot, desktopTaskIsolation: desktopTaskIsolation);
    final steps = <TurnStep>[];
    // F21: only a `done` frame says the turn finished. A stream that simply ends without one was
    // cut short (a proxy closing the connection, a server restart) and must not read as complete.
    var sawDone = false;

    // Keeps what already arrived instead of swapping it for the error bubble: the user has read
    // it, and a dropped connection does not make it wrong (F-31).
    Future<_SseResult> keepPartial(String reason, {String? error}) async {
      await _updateMessageText(assistantMsgId, displayText);
      await _updateMessageTurnMeta(
        assistantMsgId,
        meta.copyWith(steps: List.of(steps), interrupted: true),
      );
      await _logTelemetry(
        eventType: "interrupted",
        isOffline: false,
        latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
        modelUsed: "server-sse",
        errorMessage: error == null ? reason : '$reason: $error',
      );
      return const _SseResult(_SseSendOutcome.interrupted);
    }

    StreamIterator<SseChatEvent>? events;
    try {
      final client = _ref.read(sseAgentClientProvider);
      final stream = client.streamAgentEvents(
        message: message,
        sessionId: sessionId,
        agentId: agentId,
        useKnowledge: useRag,
        useMemory: useMemory,
        useTools: useTools,
        approvalIds: approvalIds,
        images: images,
        workspaceRoot: workspaceRoot,
        desktopTaskIsolation: desktopTaskIsolation,
      );

      events = StreamIterator(stream);
      _activeStream = events;
      // Stop pressed while this turn was still saving its placeholder: nothing to wait for.
      if (_stoppedMessageId == assistantMsgId) unawaited(events.cancel().catchError((_) {}));

      while (await events.moveNext()) {
        final event = events.current;
        switch (event.kind) {
          case SseEventKind.token:
            displayText += event.text ?? "";
            final now = DateTime.now();
            if (now.difference(lastDbUpdate).inMilliseconds > 500) {
              await _updateMessageText(assistantMsgId, displayText, enqueue: false);
              lastDbUpdate = now;
            }
            break;
          case SseEventKind.toolCalls:
            for (final call in event.actions ?? []) {
              if (call is Map<String, dynamic>) {
                toolCalls.add({...call, 'settled': false});
              }
            }
            await _updateMessageToolCalls(assistantMsgId, toolCalls);
            break;
          case SseEventKind.step:
            if (event.step != null) {
              // G-14d: a later frame for the same `step_id` is that step's new status.
              upsertStep(steps, event.step!);
              meta = meta.copyWith(steps: List.of(steps));
              await _updateMessageTurnMeta(assistantMsgId, meta);
            }
            break;
          case SseEventKind.actions:
            _ref.read(pendingActionsProvider.notifier).setActions(event.actions ?? []);
            break;
          case SseEventKind.citations:
            citations
              ..clear()
              ..addAll((event.citations ?? []).map((c) => c.toString()));
            await _updateMessageCitations(assistantMsgId, citations);
            break;
          case SseEventKind.revision:
            displayText = event.text ?? displayText;
            meta = meta.copyWith(revised: true);
            await _updateMessageText(assistantMsgId, displayText);
            await _updateMessageTurnMeta(assistantMsgId, meta);
            break;
          case SseEventKind.error:
            debugPrint("[ChatRepository] SSE stream error event: ${event.message}");
            if (event.details != null) {
              debugPrint("[ChatRepository] SSE stream error details: ${event.details}");
            }
            if (event.errorKind == 'safety_violation' || event.postHoc) {
              // Never keep a guarded answer as a partial (F-31's rule is for dropped
              // connections): it would stay on screen and sync to every device.
              final withheld = event.postHoc || displayText.isNotEmpty;
              displayText = '';
              await _logTelemetry(
                eventType: "error",
                isOffline: false,
                latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
                modelUsed: "server-sse",
                errorMessage: withheld ? 'safety_violation: post_hoc' : 'safety_violation',
              );
              return _SseResult(withheld ? _SseSendOutcome.withheld : _SseSendOutcome.blocked);
            }
            final engineerRefusal = _engineerRefusal(event.errorKind);
            if (engineerRefusal != null) {
              // Refused before any provider call, so there is no partial text to keep.
              await _logTelemetry(
                eventType: "error",
                isOffline: false,
                latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
                modelUsed: "server-sse",
                errorMessage: event.errorKind,
              );
              return _SseResult(engineerRefusal, null, event.details);
            }
            if (displayText.isNotEmpty) {
              return keepPartial("stream_error", error: event.message);
            }
            if (event.errorKind == 'capability_unavailable') {
              await _logTelemetry(
                eventType: "error",
                isOffline: false,
                latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
                modelUsed: "server-sse",
                errorMessage: 'capability_unavailable',
              );
              return const _SseResult(_SseSendOutcome.unavailable);
            }
            await _logTelemetry(
              eventType: "error",
              isOffline: false,
              latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
              modelUsed: "server-sse",
              errorMessage: event.message,
            );
            return _SseResult(_SseSendOutcome.failed, event.message);
          case SseEventKind.done:
            sawDone = true;
            if (citations.isEmpty && (event.citations ?? []).isNotEmpty) {
              citations.addAll(event.citations!.map((c) => c.toString()));
              await _updateMessageCitations(assistantMsgId, citations);
            }
            for (final call in toolCalls) {
              call['settled'] = true;
            }
            if (toolCalls.isNotEmpty) {
              await _updateMessageToolCalls(assistantMsgId, toolCalls);
            }
            final done = event.turnMeta ?? const TurnMeta();
            // G-14d: the turn is over, so nothing it reported as running still is.
            for (var i = 0; i < steps.length; i++) {
              if (steps[i].status == 'running') steps[i] = steps[i].withStatus(kStepUnfinished);
            }
            meta = done.copyWith(
              revised: meta.revised,
              steps: steps,
              // Client-side: where the turn ran, so approving its held calls after a restart
              // resumes it in the same folder and worktree (G-14a, WP4).
              workspaceRoot: workspaceRoot,
              desktopTaskIsolation: desktopTaskIsolation,
              // The message id doubles as this turn's scoping key for pending confirmations --
              // stable even when the backend's own `turn_id` is absent (an un-upgraded backend).
              turnId: done.turnId ?? assistantMsgId,
            );
            await _updateMessageTurnMeta(assistantMsgId, meta);
            if (meta.pendingToolCalls.isNotEmpty) {
              // The backend's `pending_tool_calls` (the actual confirm-gated calls, with their
              // real arguments) replaces the old guess from `toolCalls` -- those are the native,
              // informational-only tool-call frames (ADR-006, never executed), which merely
              // often happened to line up with the real confirm-gated tool by coincidence.
              // `addPending` (not `setPending`): an earlier turn's still-unresolved confirmation
              // must not be silently dropped just because a later turn also produced one.
              _ref.read(pendingConfirmationProvider.notifier).addPending(
                    meta.pendingToolCalls
                        .map((p) => p.copyWith(turnId: meta.turnId))
                        .toList(),
                  );
            }
            // No name-only fallback any more: a tool name cannot approve anything (F-04), so a
            // guessed entry from informational `tool_calls` frames could never be confirmed.
            break;
          case SseEventKind.unknown:
            debugPrint("[ChatRepository] ignoring unrecognised SSE frame type: ${event.unknownType}");
            break;
        }
      }

      if (_stoppedMessageId == assistantMsgId) return keepPartial("stopped_by_user");
      if (!sawDone) {
        if (displayText.isNotEmpty) return keepPartial("ended_without_done");
        await _logTelemetry(
          eventType: "failover",
          isOffline: false,
          latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
          modelUsed: "server-sse",
          errorMessage: "ended_without_done",
        );
        return const _SseResult(_SseSendOutcome.failed);
      }
      await _updateMessageText(assistantMsgId, displayText);
      await _logTelemetry(eventType: "request", isOffline: false, latencyMs: stopwatch.elapsedMilliseconds.toDouble(), modelUsed: "server-sse");
      return const _SseResult(_SseSendOutcome.succeeded);
    } on AuraApiError catch (e) {
      debugPrint("[ChatRepository] SSE path failed: $e");
      // The same refusals as an HTTP status (403 / 429) before the stream opened.
      final engineerRefusal = _engineerRefusal(e.error);
      if (engineerRefusal != null && displayText.isEmpty) {
        await _logTelemetry(
          eventType: "error",
          isOffline: false,
          latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
          modelUsed: "server-sse",
          errorMessage: e.error,
        );
        return _SseResult(engineerRefusal, null, e.details);
      }
      if (displayText.isNotEmpty || _stoppedMessageId == assistantMsgId) {
        return keepPartial("stream_failed", error: e.message);
      }
      if (e.statusCode == 402 && e.error == 'plan_limit') {
        // Refused before generating: not a connection problem, and retrying cannot help.
        return _SseResult(_SseSendOutcome.planLimit, chatPlanLimitText(e));
      }
      await _logTelemetry(
        eventType: "failover",
        isOffline: false,
        latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
        modelUsed: "server-sse",
        errorMessage: e.message,
      );
      return _SseResult(_SseSendOutcome.failed, e.message);
    } catch (e) {
      debugPrint("[ChatRepository] SSE path failed: $e");
      // Includes the idle timeout (`ChatStreamIdleTimeoutException`) after a partial answer.
      if (displayText.isNotEmpty || _stoppedMessageId == assistantMsgId) {
        return keepPartial("stream_failed", error: e.toString());
      }
      await _logTelemetry(
        eventType: "failover",
        isOffline: false,
        latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
        modelUsed: "server-sse",
        errorMessage: e.toString(),
      );
      return const _SseResult(_SseSendOutcome.failed);
    } finally {
      if (identical(_activeStream, events)) _activeStream = null;
      // Ends the generator (and its connection) on every early return above; a no-op once the
      // stream has finished on its own.
      if (events != null) unawaited(events.cancel().catchError((_) {}));
    }
  }

  /// Records a user-initiated connection-issue report in local telemetry.
  /// Does not claim a remote ticket was opened.
  Future<void> reportConnectionIssue({
    required String conversationId,
    required String messageId,
  }) async {
    await _logTelemetry(
      eventType: "user_report",
      isOffline: false,
      latencyMs: 0,
      modelUsed: "server-sse",
      errorMessage: "connection_issue conversation=$conversationId message=$messageId",
    );
  }

  Future<void> _logTelemetry({
    required String eventType,
    required bool isOffline,
    required double latencyMs,
    required String modelUsed,
    String? errorMessage,
  }) async {
    try {
      await _db.insertTelemetry(TelemetryLogsCompanion.insert(
        id: _uuid.v4(),
        eventType: eventType,
        latencyMs: latencyMs,
        agentUsed: "chat_repository",
        modelUsed: modelUsed,
        isOffline: isOffline,
        errorMessage: Value(errorMessage),
        createdAt: DateTime.now(),
      ));
    } catch (e) {
      debugPrint("[ChatRepository] Failed to log telemetry: $e");
    }
  }

  // Updates message content in Drift database
  Future<void> _updateMessageText(String messageId, String text, {bool enqueue = true}) async {
    await (_db.update(_db.messages)..where((m) => m.id.equals(messageId))).write(
      MessagesCompanion(
        content: Value(text),
        updatedAt: Value(DateTime.now()),
      ),
    );
    if (enqueue) await _enqueueUpsert(messageId);
  }

  // Persists RAG citation labels for a message, called each time the `citations` (or, as a
  // fallback, `done`) SSE frame arrives.
  Future<void> _updateMessageCitations(String messageId, List<String> citations) async {
    await (_db.update(_db.messages)..where((m) => m.id.equals(messageId))).write(
      MessagesCompanion(
        citationsJson: Value(jsonEncode(citations)),
        updatedAt: Value(DateTime.now()),
      ),
    );
    await _enqueueUpsert(messageId);
  }

  // Persists the in-flight tool-call list for a message. Called on every `tool_calls` frame
  // (so a chip appears while the turn is still streaming) and once more on `done` to flip
  // every call's `settled` flag -- there is no per-tool completion signal to key off of, so
  // "settled" means "the turn finished", not "this specific call finished".
  Future<void> _updateMessageTurnMeta(String messageId, TurnMeta meta) async {
    await (_db.update(_db.messages)..where((m) => m.id.equals(messageId))).write(
      MessagesCompanion(
        turnId: Value(meta.turnId),
        turnMetaJson: Value(meta.encode()),
        updatedAt: Value(DateTime.now()),
      ),
    );
    await _enqueueUpsert(messageId);
  }

  Future<void> _updateMessageToolCalls(String messageId, List<Map<String, dynamic>> toolCalls) async {
    await (_db.update(_db.messages)..where((m) => m.id.equals(messageId))).write(
      MessagesCompanion(
        toolCallsJson: Value(jsonEncode(toolCalls)),
        updatedAt: Value(DateTime.now()),
      ),
    );
    await _enqueueUpsert(messageId);
  }

  // Delete Conversation session history
  //
  // Local rows, their sync copies (tombstones through the outbox), and the
  // server's transcript of the session, which the assistant re-reads on every
  // later turn of it. That last part used to be missing: a deleted chat's
  // history kept being injected into new turns (audit F-07). It is retried by
  // [RemoteDeletes] until the server confirms it.
  Future<void> clearConversation(String conversationId) async {
    final rows = await (_db.select(_db.messages)..where((m) => m.conversationId.equals(conversationId))).get();
    await (_db.delete(_db.messages)..where((m) => m.conversationId.equals(conversationId))).go();
    for (final row in rows) {
      await _enqueueDelete(row.id);
    }
    await _ref.read(remoteDeletesProvider).request(RemoteDeleteKind.chatSession, conversationId);
  }
}

final chatRepositoryProvider = Provider<ChatRepository>((ref) {
  final db = ref.watch(databaseProvider);
  return ChatRepository(db, ref);
});

/// G-14a: brings back a conversation's unanswered held calls (still inside the server's TTL)
/// the first time it is shown in this app session. The chat screens watch it for the open
/// conversation; it runs once per conversation.
final pendingConfirmationRehydrationProvider = FutureProvider.family<void, String>(
  (ref, conversationId) => ref.read(chatRepositoryProvider).rehydratePendingConfirmations(conversationId),
);

/// See [ChatRepository.watchLatestAssistantId]: which of a conversation's failed turns may still
/// offer Retry. Not retried on error: the bubble then simply keeps its Retry, as before.
final latestAssistantMessageIdProvider = StreamProvider.autoDispose.family<String?, String>(
  (ref, conversationId) => ref.watch(chatRepositoryProvider).watchLatestAssistantId(conversationId),
  retry: (_, _) => null,
);

// Active Conversation session ID
class ActiveConversation extends Notifier<String> {
  @override
  String build() => 'default_session';
  void setSession(String id) => state = id;
}

final activeConversationIdProvider = NotifierProvider<ActiveConversation, String>(() {
  return ActiveConversation();
});

/// How many of a conversation's most recent messages are loaded, per conversation. Starts at
/// [ChatVisibleMessageLimit.pageSize] and grows via [ChatVisibleMessageLimit.loadMore] --
/// bounding the initial decrypt cost of opening a long conversation (see [ChatRepository.watchMessages]).
class ChatVisibleMessageLimit extends Notifier<int> {
  ChatVisibleMessageLimit(this.conversationId);

  final String conversationId;
  static const int pageSize = 60;

  @override
  int build() => pageSize;

  void loadMore() => state += pageSize;
}

final chatVisibleMessageLimitProvider =
    NotifierProvider.family<ChatVisibleMessageLimit, int, String>(
        (conversationId) => ChatVisibleMessageLimit(conversationId));

// Messages List stream provider
final chatMessagesStreamProvider = StreamProvider.family.autoDispose<List<Message>, String>((ref, conversationId) {
  final repository = ref.watch(chatRepositoryProvider);
  final limit = ref.watch(chatVisibleMessageLimitProvider(conversationId));
  return repository.watchMessages(conversationId, limit: limit);
});

/// Leaves out sessions whose delete is still inside its undo window -- see
/// undoable_deletes.dart for why the delete itself waits.
Stream<List<String>> _withoutPendingDeletes(Ref ref, Stream<List<String>> sessions) {
  final pendingDeletes = ref.watch(undoableDeletesProvider);
  if (pendingDeletes.isEmpty) return sessions;
  return sessions.map(
    (list) => list.where((s) => !pendingDeletes.contains(chatSessionUndoKey(s))).toList(),
  );
}

// Conversations list stream provider
final conversationsStreamProvider = StreamProvider.autoDispose<List<String>>((ref) {
  final repository = ref.watch(chatRepositoryProvider);
  return _withoutPendingDeletes(ref, repository.watchConversations());
});

/// Conversations involving one specific persona -- see [ChatRepository.watchConversationsForAgent].
final agentConversationsStreamProvider =
    StreamProvider.family.autoDispose<List<String>, String>((ref, agentId) {
  final repository = ref.watch(chatRepositoryProvider);
  return _withoutPendingDeletes(ref, repository.watchConversationsForAgent(agentId));
});
