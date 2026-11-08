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
import 'agent_config_repository.dart';
import 'chat_connection_error.dart';
import 'turn_meta.dart';

// `interrupted`: the stream ended early (Stop, a drop, or the idle timeout) and whatever text had
// arrived was kept, marked [TurnMeta.interrupted] -- never replaced by the error bubble.
/// `unavailable`: the backend refused the turn's content class (`capability_unavailable`, the
/// chat-only launch). Not a connection error, so it gets plain copy and no Retry card.
enum _SseSendOutcome { succeeded, failed, interrupted, unavailable }

class _SseResult {
  final _SseSendOutcome outcome;
  final String? message;
  const _SseResult(this.outcome, [this.message]);
}

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
  String? _stoppedMessageId;

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

  // Sends message and streams response, saving to Drift DB in real-time
  Future<void> sendChatMessage({
    required String conversationId,
    required String userContent,
    bool useRag = true,
    bool useMemory = true,
    bool useTools = true,
    List<String> approvalIds = const [],
    bool persistUserMessage = true,
    List<ChatImageAttachment> images = const [],
  }) async {
    if (_ref.read(streamingMessageIdProvider) != null) {
      debugPrint("[ChatRepository] ignoring send while a stream is already in flight");
      return;
    }

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
    final assistantMsgId = _uuid.v4();
    final activeAgentId = _ref.read(activeAgentIdProvider);
    await _db.into(_db.messages).insert(
      MessagesCompanion.insert(
        id: assistantMsgId,
        conversationId: conversationId,
        role: "assistant",
        content: "",
        modelUsed: Value(activeAgentId),
        agentId: Value(activeAgentId),
        createdAt: DateTime.now(),
        updatedAt: Value(DateTime.now()),
      ),
    );
    await _enqueueUpsert(assistantMsgId);

    // 3. Stream against the v3 kernel. Conversation history is loaded server-side
    // from episodic memory keyed by session_id. SSE errors stay a retry bubble;
    // there is no v1 orchestrator fallback.
    _ref.read(streamingMessageIdProvider.notifier).set(assistantMsgId);
    try {
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
      );

      if (sseOutcome.outcome == _SseSendOutcome.interrupted) {
        // Partial text, already saved and marked by `_sendViaSse`.
      } else if (sseOutcome.outcome == _SseSendOutcome.unavailable) {
        // Deliberately without `kChatConnectionErrorPrefix`: that prefix is what puts a Retry
        // card on the row, and retrying this turn reaches the same refusal.
        await _updateMessageText(assistantMsgId, kChatFeatureUnavailableText);
      } else if (sseOutcome.message != null) {
        await _updateMessageText(
          assistantMsgId,
          '$kChatConnectionErrorPrefix: ${sseOutcome.message}',
        );
      } else if (sseOutcome.outcome == _SseSendOutcome.failed) {
        await _updateMessageText(assistantMsgId, kChatConnectionErrorMessage);
      }
    } finally {
      if (_stoppedMessageId == assistantMsgId) _stoppedMessageId = null;
      if (_ref.read(streamingMessageIdProvider) == assistantMsgId) {
        _ref.read(streamingMessageIdProvider.notifier).set(null);
      }
    }
  }

  /// Re-sends the last user turn after a connection-error bubble. Deletes the
  /// error row and that user row, then calls [sendChatMessage] so the UI never
  /// needs to touch Drift or `ref` after the bubble unmounts.
  Future<void> retryLastFailedTurn({
    required String conversationId,
    required String errorMessageId,
  }) async {
    final messages = await (_db.select(_db.messages)
          ..where((m) => m.conversationId.equals(conversationId))
          ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.asc)]))
        .get();

    final userMessages = messages.where((m) => m.role == "user").toList();
    if (userMessages.isEmpty) return;

    final lastUserMsg = userMessages.last;
    // The stored text carries the image marker, not the images (ADR-031): resend the text alone,
    // with the images themselves while this session still holds them.
    final images = _ref.read(sentChatImagesProvider)[lastUserMsg.id] ?? const <ChatImageAttachment>[];
    await (_db.delete(_db.messages)..where((m) => m.id.equals(errorMessageId))).go();
    await _enqueueDelete(errorMessageId);
    await (_db.delete(_db.messages)..where((m) => m.id.equals(lastUserMsg.id))).go();
    await _enqueueDelete(lastUserMsg.id);

    await sendChatMessage(
      conversationId: conversationId,
      userContent: splitImagesNote(lastUserMsg.content).text,
      images: images,
    );
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
  /// Pending entries are removed only after the request has been sent.
  Future<bool> confirmPendingCalls({
    required String conversationId,
    required List<PendingToolCall> calls,
  }) async {
    if (_ref.read(streamingMessageIdProvider) != null) return false;
    final approvable = calls.where((c) => c.canConfirm && c.turnId != null).toList();
    if (approvable.isEmpty) return false;
    final turnId = approvable.first.turnId!;
    if (approvable.any((c) => c.turnId != turnId)) return false;

    final userContent = await _userMessageForTurn(conversationId, turnId);
    if (userContent == null) return false;
    // Again after the await: `sendChatMessage` drops a send while a stream is in flight, and
    // the entries below must not be removed for a send that never happened.
    if (_ref.read(streamingMessageIdProvider) != null) return false;

    await sendChatMessage(
      conversationId: conversationId,
      userContent: userContent,
      approvalIds: approvable.map((c) => c.approvalId!).toList(),
      persistUserMessage: false,
    );
    // The server consumed that whole held turn: the approved calls ran, and anything still
    // needing approval came back under the new turn with fresh ids. The old entries are dead.
    _ref.read(pendingConfirmationProvider.notifier).removeByTurnId(turnId);
    return true;
  }

  /// The user message that produced the assistant bubble of [turnId] (its `turn_id`, or the
  /// message id standing in for one), or null when that turn is no longer in the conversation.
  Future<String?> _userMessageForTurn(String conversationId, String turnId) async {
    final messages = await (_db.select(_db.messages)
          ..where((m) => m.conversationId.equals(conversationId))
          ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.asc)]))
        .get();
    final index = messages.indexWhere((m) =>
        m.role == 'assistant' &&
        (m.id == turnId || TurnMeta.decode(m.turnMetaJson).turnId == turnId));
    for (var i = index - 1; i >= 0; i--) {
      // The server resumes the stored turn (F-04); its images were already understood then.
      if (messages[i].role == 'user') return splitImagesNote(messages[i].content).text;
    }
    return null;
  }

  /// Rejects pending calls locally -- no request is sent. The backend never receives their
  /// approval ids, so nothing runs and the held turn simply expires server-side.
  void rejectPendingCalls(List<PendingToolCall> calls) {
    final notifier = _ref.read(pendingConfirmationProvider.notifier);
    for (final call in calls) {
      if (call.approvalId != null) {
        notifier.removeByApprovalId(call.approvalId!);
      } else if (call.callId != null) {
        notifier.removeByCallId(call.callId!);
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
  }) async {
    final stopwatch = Stopwatch()..start();
    String displayText = "";
    DateTime lastDbUpdate = DateTime.now();
    final citations = <String>[];
    final toolCalls = <Map<String, dynamic>>[];
    var meta = const TurnMeta();
    final steps = <TurnStep>[];

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
              steps.add(event.step!);
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
            meta = done.copyWith(
              revised: meta.revised,
              steps: steps,
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
      await _updateMessageText(assistantMsgId, displayText);
      await _logTelemetry(eventType: "request", isOffline: false, latencyMs: stopwatch.elapsedMilliseconds.toDouble(), modelUsed: "server-sse");
      return const _SseResult(_SseSendOutcome.succeeded);
    } on AuraApiError catch (e) {
      debugPrint("[ChatRepository] SSE path failed: $e");
      if (displayText.isNotEmpty || _stoppedMessageId == assistantMsgId) {
        return keepPartial("stream_failed", error: e.message);
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
