import 'dart:convert';
import 'dart:async';
import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import '../../../core/database/database.dart';
import '../../../core/database/database_provider.dart';
import '../../../core/ai/orchestrator/agent_orchestrator.dart';
import '../../../core/ai/ai_router/aura_ai_router.dart';
import '../../../core/ai/online_agents/sse_agent_client.dart';
import 'agent_config_repository.dart';
import 'memory_repository.dart';

class PendingActions extends Notifier<List<dynamic>> {
  @override
  List<dynamic> build() => [];
  
  void setActions(List<dynamic> actions) => state = actions;
  void clear() => state = [];
}

final pendingActionsProvider = NotifierProvider<PendingActions, List<dynamic>>(() {
  return PendingActions();
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
    String? localApiKey, // Optional fallback API key
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
        createdAt: DateTime.now(),
      ),
    );

    // 4. Load agent state
    final customInstructions = _ref.read(allAgentInstructionsProvider).value ?? {};

    // 5. Prepare messages history format
    final historyPayload = historyMessages.map((m) => {
      "role": m.role,
      "content": m.content,
    }).toList();
    historyPayload.add({
      "role": "user",
      "content": userContent,
    });

    // 6. Execute streaming. When the router is online, try the v2 SSE
    // protocol first (Doc/V2_BLUEPRINT.md §7) — structured token/tool_calls
    // events instead of string-splitting a "[ACTIONS]" marker out of the
    // stream. Falls back to the existing ClientAgentOrchestrator path
    // (which already handles offline routing/failover) on any SSE error,
    // so a backend that hasn't deployed /api/v2/chat yet still works.
    final route = await _ref.read(aiRouter).selectRoute();
    final isOnline = route["provider"] == AIProvider.gemini;

    bool sseSucceeded = false;
    if (isOnline) {
      sseSucceeded = await _sendViaSse(
        agentId: activeAgentId,
        historyPayload: historyPayload,
        customInstructions: customInstructions,
        useRag: useRag,
        assistantMsgId: assistantMsgId,
      );
    }

    if (!sseSucceeded) {
      await _sendViaOrchestrator(
        agentId: activeAgentId,
        historyPayload: historyPayload,
        customInstructions: customInstructions,
        useRag: useRag,
        assistantMsgId: assistantMsgId,
      );
    }

    // Asynchronously trigger memory extraction regardless of which path served the response
    unawaited(_ref.read(memoryRepositoryProvider).triggerMemoryExtraction(historyMessages));
  }

  /// Returns true if the SSE stream completed successfully (even if the
  /// model's reply was itself an error frame) — false signals the caller
  /// to fall back to [_sendViaOrchestrator].
  Future<bool> _sendViaSse({
    required String agentId,
    required List<Map<String, String>> historyPayload,
    required Map<String, String> customInstructions,
    required bool useRag,
    required String assistantMsgId,
  }) async {
    final stopwatch = Stopwatch()..start();
    String displayText = "";
    DateTime lastDbUpdate = DateTime.now();

    try {
      final client = _ref.read(sseAgentClientProvider);
      final stream = client.streamAgentEvents(
        agentId: agentId,
        messages: historyPayload,
        customInstructions: customInstructions,
        useRag: useRag,
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
            _ref.read(pendingActionsProvider.notifier).setActions(event.actions ?? []);
            break;
          case SseEventKind.error:
            displayText = displayText.isEmpty ? "Error: ${event.message}" : displayText;
            debugPrint("[ChatRepository] SSE stream error event: ${event.message}");
            break;
          case SseEventKind.done:
            break;
        }
      }

      await _updateMessageText(assistantMsgId, displayText);
      await _logTelemetry(eventType: "request", isOffline: false, latencyMs: stopwatch.elapsedMilliseconds.toDouble(), modelUsed: "gemini-2.5-flash-sse");
      return true;
    } catch (e) {
      debugPrint("[ChatRepository] SSE path failed, falling back: $e");
      await _logTelemetry(
        eventType: "failover",
        isOffline: false,
        latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
        modelUsed: "gemini-2.5-flash-sse",
        errorMessage: e.toString(),
      );
      return false;
    }
  }

  /// V1-era path: ClientAgentOrchestrator → AuraAIRouter, which itself
  /// picks online/offline and handles failover, still parsing a trailing
  /// "[ACTIONS]" marker out of the raw text stream. Kept as the fallback
  /// for offline mode and for when /api/v2/chat is unavailable.
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
      fullResponseText = "Connection Error: Failed to retrieve response from active AI agent.";
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
