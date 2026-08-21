import 'dart:convert';
import 'dart:async';
import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import '../../../core/database/database.dart';
import '../../../core/database/database_provider.dart';
import '../../../core/ai/orchestrator/agent_orchestrator.dart';
import '../../../core/ai/online_agents/sse_agent_client.dart';
import 'agent_config_repository.dart';
import 'chat_connection_error.dart';
import 'memory_repository.dart';

enum _SseSendOutcome { succeeded, connectionFailure, otherFailure }

class PendingActions extends Notifier<List<dynamic>> {
  @override
  List<dynamic> build() => [];
  
  void setActions(List<dynamic> actions) => state = actions;
  void clear() => state = [];
}

final pendingActionsProvider = NotifierProvider<PendingActions, List<dynamic>>(() {
  return PendingActions();
});

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

  // Sends message and streams response, saving to Drift DB in real-time
  Future<void> sendChatMessage({
    required String conversationId,
    required String userContent,
    bool useRag = true,
  }) async {
    // Clear any previous pending actions first
    _ref.read(pendingActionsProvider.notifier).clear();

    // 1. Save User Message
    final userMsgId = _uuid.v4();
    await _db.into(_db.messages).insert(
      MessagesCompanion.insert(
        id: userMsgId,
        conversationId: conversationId,
        role: "user",
        content: userContent,
        createdAt: DateTime.now(),
      ),
    );

    // 2. Load Conversation History
    final historyMessages = await (_db.select(_db.messages)
          ..where((m) => m.conversationId.equals(conversationId))
          ..orderBy([(m) => OrderingTerm(expression: m.createdAt, mode: OrderingMode.asc)]))
        .get();

    // 3. Save Placeholder Assistant Message
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
      ),
    );

    // 4. Execute streaming against the v3 kernel (aura_core/api/v3.py's /chat/stream). Unlike the
    // legacy v1/v2 path, v3 loads conversation history itself from server-side episodic memory
    // keyed by session_id, so the client sends only the latest message. Falls back to the v1
    // orchestrator path (which does need a history payload) on SSE-level errors that are not a
    // dead host — a connection/socket failure would hit the same unreachable backend twice.
    _ref.read(streamingMessageIdProvider.notifier).set(assistantMsgId);
    try {
      final sseOutcome = await _sendViaSse(
        message: userContent,
        sessionId: conversationId,
        agentId: activeAgentId,
        useRag: useRag,
        assistantMsgId: assistantMsgId,
      );

      if (sseOutcome == _SseSendOutcome.otherFailure) {
        // Legacy fallback still needs a message-history array and per-agent custom instructions,
        // neither of which the v3 path above uses.
        final customInstructions = _ref.read(allAgentInstructionsProvider).value ?? {};
        final historyPayload = historyMessages.map((m) => {
          "role": m.role,
          "content": m.content,
        }).toList();
        historyPayload.add({
          "role": "user",
          "content": userContent,
        });
        await _sendViaOrchestrator(
          agentId: activeAgentId,
          historyPayload: historyPayload,
          customInstructions: customInstructions,
          useRag: useRag,
          assistantMsgId: assistantMsgId,
        );
      } else if (sseOutcome == _SseSendOutcome.connectionFailure) {
        await _updateMessageText(assistantMsgId, kChatConnectionErrorMessage);
      }
    } finally {
      if (_ref.read(streamingMessageIdProvider) == assistantMsgId) {
        _ref.read(streamingMessageIdProvider.notifier).set(null);
      }
    }

    final assistantRow = await (_db.select(_db.messages)
          ..where((m) => m.id.equals(assistantMsgId)))
        .getSingleOrNull();
    if (assistantRow != null && !isChatConnectionErrorText(assistantRow.content)) {
      unawaited(_ref.read(memoryRepositoryProvider).triggerMemoryExtraction(historyMessages));
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
    await (_db.delete(_db.messages)..where((m) => m.id.equals(lastUserMsg.id))).go();

    await sendChatMessage(
      conversationId: conversationId,
      userContent: lastUserMsg.content,
    );
  }

  /// Returns [_SseSendOutcome.succeeded] if the SSE stream completed
  /// (even if the model's reply was itself an error frame).
  /// [_SseSendOutcome.connectionFailure] means the host was unreachable —
  /// the caller must not fall back to the orchestrator (same dead host).
  /// [_SseSendOutcome.otherFailure] signals a fall back to [_sendViaOrchestrator].
  ///
  /// Deliberately does not write to [pendingActionsProvider]: v3's tool
  /// registry (aura_core/tools/builtin.py) has no task/calendar/finance/
  /// health CRUD tools, so that panel would only ever be empty on this
  /// path. It stays populated exclusively by [_sendViaOrchestrator], the
  /// v1 fallback whose backend agent still proposes those actions.
  Future<_SseSendOutcome> _sendViaSse({
    required String message,
    required String sessionId,
    required String agentId,
    required bool useRag,
    required String assistantMsgId,
  }) async {
    final stopwatch = Stopwatch()..start();
    String displayText = "";
    DateTime lastDbUpdate = DateTime.now();
    final citations = <String>[];
    final toolCalls = <Map<String, dynamic>>[];

    try {
      final client = _ref.read(sseAgentClientProvider);
      final stream = client.streamAgentEvents(
        message: message,
        sessionId: sessionId,
        agentId: agentId,
        useKnowledge: useRag,
      );

      await for (final event in stream) {
        switch (event.kind) {
          case SseEventKind.token:
            displayText += event.text ?? "";
            final now = DateTime.now();
            if (now.difference(lastDbUpdate).inMilliseconds > 500) {
              await _updateMessageText(assistantMsgId, displayText);
              lastDbUpdate = now;
            }
            break;
          case SseEventKind.toolCalls:
            for (final call in event.actions ?? []) {
              if (call is Map<String, dynamic>) {
                toolCalls.add({...call, 'settled': false});
              }
            }
            // Written immediately (not throttled like token text) so a chip
            // appears while the turn is still streaming, not only at the end.
            await _updateMessageToolCalls(assistantMsgId, toolCalls);
            break;
          case SseEventKind.citations:
            citations
              ..clear()
              ..addAll((event.citations ?? []).map((c) => c.toString()));
            await _updateMessageCitations(assistantMsgId, citations);
            break;
          case SseEventKind.revision:
            // A full-text replacement (post-hoc safety-guard redaction), not an append.
            displayText = event.text ?? displayText;
            await _updateMessageText(assistantMsgId, displayText);
            break;
          case SseEventKind.error:
            displayText = displayText.isEmpty ? "Error: ${event.message}" : displayText;
            debugPrint("[ChatRepository] SSE stream error event: ${event.message}");
            break;
          case SseEventKind.done:
            // Safety net: use done's citations if the dedicated frame never arrived.
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
            break;
          case SseEventKind.unknown:
            debugPrint("[ChatRepository] ignoring unrecognised SSE frame type: ${event.unknownType}");
            break;
        }
      }

      await _updateMessageText(assistantMsgId, displayText);
      await _logTelemetry(eventType: "request", isOffline: false, latencyMs: stopwatch.elapsedMilliseconds.toDouble(), modelUsed: "server-sse");
      return _SseSendOutcome.succeeded;
    } catch (e) {
      final unreachable = isUnreachableBackendError(e);
      debugPrint(unreachable
          ? "[ChatRepository] SSE path failed (host unreachable), skipping orchestrator: $e"
          : "[ChatRepository] SSE path failed, falling back: $e");
      await _logTelemetry(
        eventType: "failover",
        isOffline: false,
        latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
        modelUsed: "server-sse",
        errorMessage: e.toString(),
      );
      return unreachable
          ? _SseSendOutcome.connectionFailure
          : _SseSendOutcome.otherFailure;
    }
  }

  /// V1-era path: ClientAgentOrchestrator -> AuraAIRouter -> /api/chat/agent, still parsing a
  /// trailing "[ACTIONS]" marker out of the raw text stream. Kept as the fallback for SSE-level
  /// errors that are not a dead host -- also the only path that still proposes
  /// task/calendar/finance/health actions and forwards Google Drive access, neither of
  /// which v3's tool registry has yet (see _sendViaSse's doc comment).
  Future<void> _sendViaOrchestrator({
    required String agentId,
    required List<Map<String, String>> historyPayload,
    required Map<String, String> customInstructions,
    required bool useRag,
    required String assistantMsgId,
  }) async {
    String fullResponseText = "";
    DateTime lastDbUpdate = DateTime.now();

    try {
      final orchestrator = _ref.read(clientAgentOrchestratorProvider);
      final stream = orchestrator.executeAgentStream(
        agentId: agentId,
        messages: historyPayload,
        customInstructions: customInstructions,
        useRag: useRag,
      );

      await for (final chunk in stream) {
        fullResponseText += chunk;
        String displayText = fullResponseText;
        if (fullResponseText.contains('[ACTIONS]')) {
          displayText = fullResponseText.split('[ACTIONS]')[0].trim();
        }

        // Throttled updates (max once per 500ms) to prevent UI thread blocking
        final now = DateTime.now();
        if (now.difference(lastDbUpdate).inMilliseconds > 500) {
          await _updateMessageText(assistantMsgId, displayText);
          lastDbUpdate = now;
        }
      }

      // Ensure the final chunk is persisted
      String finalDisplayText = fullResponseText;
      if (fullResponseText.contains('[ACTIONS]')) {
        finalDisplayText = fullResponseText.split('[ACTIONS]')[0].trim();
      }
      await _updateMessageText(assistantMsgId, finalDisplayText);

      // Parse tool action cards if generated by the backend
      if (fullResponseText.contains('[ACTIONS]')) {
        final parts = fullResponseText.split('[ACTIONS]');
        final actionsText = parts[1].trim();
        try {
          final List actions = jsonDecode(actionsText);
          _ref.read(pendingActionsProvider.notifier).setActions(actions);
        } catch (e) {
          debugPrint("Actions JSON parse failed: $e");
        }
      }
    } catch (e) {
      debugPrint("ClientAgentOrchestrator stream execution failed: $e");
      fullResponseText = kChatConnectionErrorMessage;
      await _updateMessageText(assistantMsgId, fullResponseText);
    }
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
  Future<void> _updateMessageText(String messageId, String text) async {
    await (_db.update(_db.messages)..where((m) => m.id.equals(messageId))).write(
      MessagesCompanion(
        content: Value(text),
      ),
    );
  }

  // Persists RAG citation labels for a message, called each time the `citations` (or, as a
  // fallback, `done`) SSE frame arrives.
  Future<void> _updateMessageCitations(String messageId, List<String> citations) async {
    await (_db.update(_db.messages)..where((m) => m.id.equals(messageId))).write(
      MessagesCompanion(
        citationsJson: Value(jsonEncode(citations)),
      ),
    );
  }

  // Persists the in-flight tool-call list for a message. Called on every `tool_calls` frame
  // (so a chip appears while the turn is still streaming) and once more on `done` to flip
  // every call's `settled` flag -- there is no per-tool completion signal to key off of, so
  // "settled" means "the turn finished", not "this specific call finished".
  Future<void> _updateMessageToolCalls(String messageId, List<Map<String, dynamic>> toolCalls) async {
    await (_db.update(_db.messages)..where((m) => m.id.equals(messageId))).write(
      MessagesCompanion(
        toolCallsJson: Value(jsonEncode(toolCalls)),
      ),
    );
  }

  // Delete Conversation session history
  Future<void> clearConversation(String conversationId) async {
    await (_db.delete(_db.messages)..where((m) => m.conversationId.equals(conversationId))).go();
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
