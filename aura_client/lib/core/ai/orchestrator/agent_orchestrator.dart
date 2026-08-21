import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../ai_router/aura_ai_router.dart';

/// Passes an agent turn to the router, which sends it to the backend.
///
/// This class used to assemble context on the device: it queried local semantic memory and the
/// local knowledge base and prepended both to the system instruction — but only when the router had
/// selected the on-device model, because "Backend handles online". With the on-device path removed,
/// every turn is served by the backend, and the backend does memory recall and retrieval itself,
/// with budgeting, trust-channel fencing and provenance the client cannot reproduce.
///
/// Keeping the client-side assembly would have meant doing that work twice and sending the results
/// as an untrusted instruction blob — which is precisely the indirect prompt-injection shape the
/// server-side guards exist to stop. So the orchestrator is now a seam, not a stage.
///
/// Chat send no longer uses this path (v3 SSE only). Connectivity status still goes through
/// the AI router.
class ClientAgentOrchestrator {
  final Ref _ref;

  ClientAgentOrchestrator(this._ref);

  Stream<String> executeAgentStream({
    required String agentId,
    required List<Map<String, String>> messages,
    Map<String, String>? customInstructions,
    bool useRag = true,
  }) {
    return _ref.read(aiRouter).routePromptStream(
          agentId: agentId,
          messages: messages,
          customInstructions: customInstructions,
          useRag: useRag,
        );
  }
}

final clientAgentOrchestratorProvider = Provider<ClientAgentOrchestrator>((ref) {
  return ClientAgentOrchestrator(ref);
});
