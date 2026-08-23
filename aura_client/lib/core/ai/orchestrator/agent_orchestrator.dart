import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../network/aura_api_error.dart';

/// Retired seam. Chat uses [SseAgentClient]. Kept so cutover tests can spy
/// that this path is never invoked.
class ClientAgentOrchestrator {
  ClientAgentOrchestrator(this._ref);

  // ignore: unused_field
  final Ref _ref;

  Stream<String> executeAgentStream({
    required String agentId,
    required List<Map<String, String>> messages,
    Map<String, String>? customInstructions,
    bool useRag = true,
  }) async* {
    throw const AuraBackendException(
      'Client orchestrator is retired. Use /api/v3/chat/stream.',
      statusCode: 410,
    );
  }
}

final clientAgentOrchestratorProvider = Provider<ClientAgentOrchestrator>((ref) {
  return ClientAgentOrchestrator(ref);
});
