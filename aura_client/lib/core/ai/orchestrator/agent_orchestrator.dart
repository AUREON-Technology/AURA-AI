import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../ai_router/aura_ai_router.dart';
import '../memory/aura_memory.dart';
import '../knowledge/knowledge_center.dart';

class ClientAgentOrchestrator {
  final Ref _ref;

  ClientAgentOrchestrator(this._ref);

  /// Orchestrates memories, standard RAG knowledge context, agent instructions, and routes to router streams
  Stream<String> executeAgentStream({
    required String agentId,
    required List<Map<String, String>> messages,
    Map<String, String>? customInstructions,
    bool useRag = true,
  }) async* {
    final lastUserMsg = messages.lastWhere(
      (m) => m["role"] == "user",
      orElse: () => <String, String>{},
    )["content"] ?? "";

    // 1. Fetch relevant user memories
    String memoryContext = "";
    if (lastUserMsg.isNotEmpty) {
      try {
        final auraMemory = _ref.read(auraMemoryProvider);
        final memories = await auraMemory.semantic.queryMemories(lastUserMsg);
        if (memories.isNotEmpty) {
          memoryContext = "\nThings AURA remembers about the user:\n" +
              memories.map((m) => "- ${m.content}").join('\n');
        }
      } catch (e) {
        debugPrint("ClientAgentOrchestrator: Memory fetch skipped: $e");
      }
    }

    // 2. Fetch relevant RAG chunks
    String ragContext = "";
    if (useRag && lastUserMsg.isNotEmpty) {
      try {
        final knowledge = _ref.read(knowledgeCenterProvider);
        final chunks = await knowledge.queryKnowledge(lastUserMsg);
        if (chunks.isNotEmpty) {
          ragContext = "\nRelevant Knowledge Base Chunks:\n" +
              chunks.map((c) => "--- Chunk:\n${c['content']}\n").join('\n');
        }
      } catch (e) {
        debugPrint("ClientAgentOrchestrator: RAG fetch skipped: $e");
      }
    }

    // 3. Combine custom instruction prompts
    final baseInstruction = customInstructions?[agentId] ?? "";
    final finalInstruction = "$baseInstruction\n$memoryContext\n$ragContext";

    final instructionsMap = {
      agentId: finalInstruction,
    };

    final router = _ref.read(aiRouter);
    yield* router.routePromptStream(
      agentId: agentId,
      messages: messages,
      customInstructions: instructionsMap,
      useRag: useRag,
    );
  }
}

final clientAgentOrchestratorProvider = Provider<ClientAgentOrchestrator>((ref) {
  return ClientAgentOrchestrator(ref);
});
