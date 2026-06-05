import 'dart:async';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../offline_agents/offline_agent_manager.dart';
import '../online_agents/online_agent_manager.dart';

enum AIProvider {
  auto,
  gemini,
  offlineFast,
  offlineReasoning,
  offlinePremium
}

class AIRouterStatus {
  final String activeModel;
  final double latencyMs;
  final String status; // e.g. "Optimal", "Degraded", "Offline Fallback"
  final bool isOffline;

  AIRouterStatus({
    required this.activeModel,
    required this.latencyMs,
    required this.status,
    required this.isOffline,
  });

  AIRouterStatus copyWith({
    String? activeModel,
    double? latencyMs,
    String? status,
    bool? isOffline,
  }) {
    return AIRouterStatus(
      activeModel: activeModel ?? this.activeModel,
      latencyMs: latencyMs ?? this.latencyMs,
      status: status ?? this.status,
      isOffline: isOffline ?? this.isOffline,
    );
  }
}

class AIRouterStatusNotifier extends Notifier<AIRouterStatus> {
  @override
  AIRouterStatus build() => AIRouterStatus(
        activeModel: 'gemini-2.5-flash',
        latencyMs: 0.0,
        status: 'Initializing',
        isOffline: false,
      );

  void updateStatus(AIRouterStatus status) {
    state = status;
  }
}

final aiRouterStatusProvider =
    NotifierProvider<AIRouterStatusNotifier, AIRouterStatus>(() {
  return AIRouterStatusNotifier();
});

class AIProviderSetting extends Notifier<AIProvider> {
  @override
  AIProvider build() => AIProvider.auto;

  void setProvider(AIProvider val) {
    state = val;
  }
}

final aiProviderSettingProvider =
    NotifierProvider<AIProviderSetting, AIProvider>(() {
  return AIProviderSetting();
});

class AuraAIRouter {
  final Ref _ref;

  AuraAIRouter(this._ref);

  /// Helper to test internet connectivity by performing a fast DNS lookup
  Future<bool> checkInternet() async {
    try {
      final result = await InternetAddress.lookup('google.com')
          .timeout(const Duration(seconds: 2));
      return result.isNotEmpty && result[0].rawAddress.isNotEmpty;
    } on SocketException catch (_) {
      return false;
    } catch (_) {
      return false;
    }
  }

  /// Evaluates routing conditions and returns the selected provider & model name
  Future<Map<String, dynamic>> selectRoute() async {
    final manualSetting = _ref.read(aiProviderSettingProvider);
    final isOnline = await checkInternet();
    
    _ref.read(aiRouterStatusProvider.notifier).updateStatus(
      _ref.read(aiRouterStatusProvider).copyWith(isOffline: !isOnline),
    );

    if (manualSetting == AIProvider.gemini) {
      return {"provider": AIProvider.gemini, "model": "gemini-2.5-flash"};
    } else if (manualSetting == AIProvider.offlineFast) {
      return {"provider": AIProvider.offlineFast, "model": "phi3"};
    } else if (manualSetting == AIProvider.offlineReasoning) {
      return {"provider": AIProvider.offlineReasoning, "model": "mistral"};
    } else if (manualSetting == AIProvider.offlinePremium) {
      return {"provider": AIProvider.offlinePremium, "model": "llama3"};
    }

    // Auto Mode Selection
    if (isOnline) {
      // Check if Gemini quota was recently exhausted or if server is failing
      // If everything is fine, route to Gemini
      return {"provider": AIProvider.gemini, "model": "gemini-2.5-flash"};
    } else {
      // Auto fall back to local fast offline model
      return {"provider": AIProvider.offlineFast, "model": "phi3"};
    }
  }

  /// Core routing function returning a stream of chunks
  Stream<String> routePromptStream({
    required String agentId,
    required List<Map<String, String>> messages,
    Map<String, String>? customInstructions,
    bool useRag = true,
  }) async* {
    final route = await selectRoute();
    final provider = route["provider"] as AIProvider;
    final modelName = route["model"] as String;

    final stopwatch = Stopwatch()..start();
    
    _ref.read(aiRouterStatusProvider.notifier).updateStatus(AIRouterStatus(
      activeModel: modelName,
      latencyMs: 0.0,
      status: provider == AIProvider.gemini ? "Online (Optimal)" : "Offline Fallback",
      isOffline: provider != AIProvider.gemini,
    ));

    if (provider == AIProvider.gemini) {
      bool hasErrors = false;
      try {
        final onlineAgent = _ref.read(onlineAgentManagerProvider);
        final stream = onlineAgent.streamResponse(
          agentId: agentId,
          messages: messages,
          customInstructions: customInstructions,
          useRag: useRag,
        );
        await for (final chunk in stream) {
          yield chunk;
        }
      } catch (e) {
        debugPrint("Gemini stream failed, triggering auto failover: $e");
        hasErrors = true;
      }

      if (hasErrors) {
        // Update state to failover
        _ref.read(aiRouterStatusProvider.notifier).updateStatus(AIRouterStatus(
          activeModel: "phi3",
          latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
          status: "Failover Triggered",
          isOffline: true,
        ));
        // Fall back to offline fast model
        final offlineAgent = _ref.read(offlineAgentManagerProvider);
        yield* offlineAgent.streamResponse(
          model: "phi3",
          messages: messages,
          systemInstruction: customInstructions?[agentId] ?? "",
        );
      }
    } else {
      final offlineAgent = _ref.read(offlineAgentManagerProvider);
      yield* offlineAgent.streamResponse(
        model: modelName,
        messages: messages,
        systemInstruction: customInstructions?[agentId] ?? "",
      );
    }
    
    stopwatch.stop();
    _ref.read(aiRouterStatusProvider.notifier).updateStatus(
      _ref.read(aiRouterStatusProvider).copyWith(
        latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
      ),
    );
  }
}

final aiRouter = Provider<AuraAIRouter>((ref) {
  return AuraAIRouter(ref);
});
