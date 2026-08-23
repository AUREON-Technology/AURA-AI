import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../network/aura_api_error.dart';

export '../../network/aura_api_error.dart' show AuraBackendException, AuraApiError;

/// Retired. Inference goes through [SseAgentClient] → `/api/v3/chat/stream`.
///
/// The class remains so imports and the [AuraBackendException] export do not
/// break. [streamResponse] always throws — there is no `/api/chat/agent`.
class OnlineAgentManager {
  OnlineAgentManager(this._ref);

  // ignore: unused_field
  final Ref _ref;

  Stream<String> streamResponse({
    required String agentId,
    required List<Map<String, String>> messages,
    Map<String, String>? customInstructions,
    bool useRag = true,
  }) async* {
    throw const AuraBackendException(
      'This path has been retired. Use Copilot, which talks to /api/v3/chat/stream.',
      statusCode: 410,
    );
  }
}

final onlineAgentManagerProvider = Provider<OnlineAgentManager>((ref) {
  return OnlineAgentManager(ref);
});
