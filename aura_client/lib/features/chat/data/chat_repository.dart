import 'dart:convert';
import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import 'package:aura_client/core/database/database.dart';
import 'package:aura_client/core/database/database_provider.dart';
import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/sync/sync_payloads.dart';
import 'package:aura_client/core/network/aura_api_error.dart';
import 'agent_config_repository.dart';
import 'chat_connection_error.dart';
import 'turn_meta.dart';

enum _SseSendOutcome { succeeded, failed }

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

  ChatRepository(this._db, this._ref);

  // Watch messages for a specific conversation session
  Stream<List<Message>> watchMessages(String conversationId) {
    return (_db.select(_db.messages)
          ..where((m) => m.conversationId.equals(conversationId))
          ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.asc)]))
        .watch();
  }

  // Watch all active conversations (grouped by conversationId, ordering by latest)
  Stream<List<String>> watchConversations() {
    final query = _db.selectOnly(_db.messages)
      ..addColumns([_db.messages.conversationId])
      ..groupBy([_db.messages.conversationId]);
    
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
    List<String> confirmedTools = const [],
    List<String> confirmedCallIds = const [],
    bool persistUserMessage = true,
  }) async {
    if (_ref.read(streamingMessageIdProvider) != null) {
      debugPrint("[ChatRepository] ignoring send while a stream is already in flight");
      return;
    }

    _ref.read(pendingActionsProvider.notifier).clear();
    // Pending confirmations are turn-scoped (each `PendingToolCall.turnId`), not cleared wholesale
    // here -- a new message must not silently discard a still-unresolved earlier turn's pending
    // calls. `confirmTaintedTools`/`rejectTools` remove entries individually as they resolve.

    if (persistUserMessage) {
      final userMsgId = _uuid.v4();
      await _db.into(_db.messages).insert(
        MessagesCompanion.insert(
          id: userMsgId,
          conversationId: conversationId,
          role: "user",
          content: userContent,
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
        confirmedTools: confirmedTools,
        confirmedCallIds: confirmedCallIds,
        assistantMsgId: assistantMsgId,
      );

      if (sseOutcome.message != null) {
        await _updateMessageText(
          assistantMsgId,
          '$kChatConnectionErrorPrefix: ${sseOutcome.message}',
        );
      } else if (sseOutcome.outcome == _SseSendOutcome.failed) {
        await _updateMessageText(assistantMsgId, kChatConnectionErrorMessage);
      }
    } finally {
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
    await (_db.delete(_db.messages)..where((m) => m.id.equals(errorMessageId))).go();
    await _enqueueDelete(errorMessageId);
    await (_db.delete(_db.messages)..where((m) => m.id.equals(lastUserMsg.id))).go();
    await _enqueueDelete(lastUserMsg.id);

    await sendChatMessage(
      conversationId: conversationId,
      userContent: lastUserMsg.content,
    );
  }

  Future<void> confirmTaintedTools({
    required String conversationId,
    required List<String> confirmedTools,
    List<String> confirmedCallIds = const [],
  }) async {
    final messages = await (_db.select(_db.messages)
          ..where((m) => m.conversationId.equals(conversationId))
          ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.asc)]))
        .get();
    final userMessages = messages.where((m) => m.role == 'user').toList();
    if (userMessages.isEmpty) return;
    for (final callId in confirmedCallIds) {
      _ref.read(pendingConfirmationProvider.notifier).removeByCallId(callId);
    }
    if (confirmedCallIds.isEmpty) {
      // Legacy all-or-nothing path: no call ids given, so every pending call sharing one of
      // these tool names is being approved -- clear them all rather than leaving stale entries.
      final notifier = _ref.read(pendingConfirmationProvider.notifier);
      for (final call in _ref.read(pendingConfirmationProvider)) {
        if (confirmedTools.contains(call.tool) && call.callId != null) {
          notifier.removeByCallId(call.callId!);
        }
      }
    }
    await sendChatMessage(
      conversationId: conversationId,
      userContent: userMessages.last.content,
      confirmedTools: confirmedTools,
      confirmedCallIds: confirmedCallIds,
      persistUserMessage: false,
    );
  }

  /// Rejects pending calls locally -- no request is sent; the backend simply never receives a
  /// confirmation for these call ids, so they stay refused on the next turn like today's
  /// "just don't confirm it" behavior, only now scoped to the specific calls the user declined.
  void rejectPendingCalls(List<String> callIds) {
    final notifier = _ref.read(pendingConfirmationProvider.notifier);
    for (final callId in callIds) {
      notifier.removeByCallId(callId);
    }
  }

  Future<_SseResult> _sendViaSse({
    required String message,
    required String sessionId,
    required String agentId,
    required bool useRag,
    required bool useMemory,
    required bool useTools,
    required List<String> confirmedTools,
    List<String> confirmedCallIds = const [],
    required String assistantMsgId,
  }) async {
    final stopwatch = Stopwatch()..start();
    String displayText = "";
    DateTime lastDbUpdate = DateTime.now();
    final citations = <String>[];
    final toolCalls = <Map<String, dynamic>>[];
    var meta = const TurnMeta();
    final steps = <TurnStep>[];

    try {
      final client = _ref.read(sseAgentClientProvider);
      final stream = client.streamAgentEvents(
        message: message,
        sessionId: sessionId,
        agentId: agentId,
        useKnowledge: useRag,
        useMemory: useMemory,
        useTools: useTools,
        confirmedTools: confirmedTools,
        confirmedCallIds: confirmedCallIds,
      );

      await for (final event in stream) {
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
            } else if (meta.needsConfirmation) {
              // Backward-compat fallback for a backend that hasn't been upgraded yet (no
              // `pending_tool_calls` field): fall back to the old name-only guess so confirmation
              // still round-trips, just without per-tool detail.
              final names = toolCalls
                  .map((c) => c['name']?.toString())
                  .whereType<String>()
                  .where((n) => n.isNotEmpty)
                  .toList();
              _ref.read(pendingConfirmationProvider.notifier).addPending(
                    names.map((n) => PendingToolCall(tool: n, turnId: meta.turnId)).toList(),
                  );
            }
            break;
          case SseEventKind.unknown:
            debugPrint("[ChatRepository] ignoring unrecognised SSE frame type: ${event.unknownType}");
            break;
        }
      }

      await _updateMessageText(assistantMsgId, displayText);
      await _logTelemetry(eventType: "request", isOffline: false, latencyMs: stopwatch.elapsedMilliseconds.toDouble(), modelUsed: "server-sse");
      return const _SseResult(_SseSendOutcome.succeeded);
    } on AuraApiError catch (e) {
      debugPrint("[ChatRepository] SSE path failed: $e");
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
      await _logTelemetry(
        eventType: "failover",
        isOffline: false,
        latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
        modelUsed: "server-sse",
        errorMessage: e.toString(),
      );
      return const _SseResult(_SseSendOutcome.failed);
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
  Future<void> clearConversation(String conversationId) async {
    final rows = await (_db.select(_db.messages)..where((m) => m.conversationId.equals(conversationId))).get();
    await (_db.delete(_db.messages)..where((m) => m.conversationId.equals(conversationId))).go();
    for (final row in rows) {
      await _enqueueDelete(row.id);
    }
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

// Messages List stream provider
final chatMessagesStreamProvider = StreamProvider.family.autoDispose<List<Message>, String>((ref, conversationId) {
  final repository = ref.watch(chatRepositoryProvider);
  return repository.watchMessages(conversationId);
});

// Conversations list stream provider
final conversationsStreamProvider = StreamProvider.autoDispose<List<String>>((ref) {
  final repository = ref.watch(chatRepositoryProvider);
  return repository.watchConversations();
});
