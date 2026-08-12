import 'dart:async';
import 'dart:io';
import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';
import '../../database/database.dart';
import '../../database/database_provider.dart';
import '../online_agents/online_agent_manager.dart';

/// Routes every AI request to the AURA backend, and reports whether it is reachable.
///
/// This class used to choose between a hosted API and an on-device model, and to fail over from
/// one to the other. Both are gone: all inference now runs on our own server, and the client is a
/// client. What remains is connection state, which is still worth tracking -- the UI shows it, and
/// "AURA is unreachable" is a different message from "AURA could not answer".
///
/// The `AIProvider` enum that used to select between engines was removed rather than reduced to a
/// single value. A settings control offering a choice that no longer exists is worse than no
/// control: it tells the user something about the system that is not true.
///
/// There is deliberately no fallback path. When the backend is unreachable the router surfaces
/// that. It does not answer from a local model, and it does not answer at all -- inventing a reply
/// because the server was down is the failure mode this whole change exists to remove.
class AIRouterStatus {
  final String activeModel;
  final double latencyMs;

  /// Human-readable connection state, shown in the UI.
  final String status;

  /// True when the last attempt could not reach the backend.
  ///
  /// Note the changed meaning: this used to signal "answering from the on-device model". It now
  /// signals "not answering", which is why nothing downstream should treat it as a degraded-but-
  /// working state.
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
        // The server decides which model serves a turn, and it may change per request under its
        // own routing policy. The client does not know the model name and should not claim one.
        activeModel: 'server',
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

class AuraAIRouter {
  final Ref _ref;

  AuraAIRouter(this._ref);

  /// Whether the device has any network path at all.
  ///
  /// A DNS lookup rather than a request to our own backend: this distinguishes "the phone is
  /// offline" from "AURA is down", which are different things to tell a user. It is only a hint --
  /// the authoritative answer is whether the request below succeeds.
  Future<bool> checkConnectivity() async {
    try {
      final result = await InternetAddress.lookup('one.one.one.one')
          .timeout(const Duration(seconds: 2));
      return result.isNotEmpty && result[0].rawAddress.isNotEmpty;
    } on SocketException catch (_) {
      return false;
    } catch (_) {
      return false;
    }
  }

  /// Streams a response from the backend, recording latency and connection state.
  ///
  /// Rethrows [AuraBackendException] so the caller can distinguish an outage from an answer. The
  /// previous implementation caught everything here and quietly switched to a local model, which
  /// meant the UI could not tell the two apart and neither could the user.
  Stream<String> routePromptStream({
    required String agentId,
    required List<Map<String, String>> messages,
    Map<String, String>? customInstructions,
    bool useRag = true,
  }) async* {
    final stopwatch = Stopwatch()..start();
    final notifier = _ref.read(aiRouterStatusProvider.notifier);

    notifier.updateStatus(AIRouterStatus(
      activeModel: 'server',
      latencyMs: 0.0,
      status: 'Contacting AURA',
      isOffline: false,
    ));

    String? errorMessage;
    var eventType = 'request';
    var unreachable = false;

    try {
      final agent = _ref.read(onlineAgentManagerProvider);
      await for (final chunk in agent.streamResponse(
        agentId: agentId,
        messages: messages,
        customInstructions: customInstructions,
        useRag: useRag,
      )) {
        yield chunk;
      }
      notifier.updateStatus(AIRouterStatus(
        activeModel: 'server',
        latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
        status: 'Online',
        isOffline: false,
      ));
    } catch (e) {
      unreachable = true;
      eventType = 'error';
      errorMessage = e.toString();
      notifier.updateStatus(AIRouterStatus(
        activeModel: 'server',
        latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
        status: 'AURA unreachable',
        isOffline: true,
      ));
      rethrow;
    } finally {
      stopwatch.stop();
      // Logged in `finally` so a failed turn is recorded too. Telemetry that only covers successes
      // is how an outage looks like a drop in traffic rather than a fault.
      await _logTelemetry(
        eventType: eventType,
        latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
        agentUsed: agentId,
        modelUsed: 'server',
        isOffline: unreachable,
        errorMessage: errorMessage,
      );
    }
  }

  Future<void> _logTelemetry({
    required String eventType,
    required double latencyMs,
    required String agentUsed,
    required String modelUsed,
    required bool isOffline,
    String? errorMessage,
  }) async {
    try {
      final db = _ref.read(databaseProvider);
      await db.insertTelemetry(TelemetryLogsCompanion.insert(
        id: const Uuid().v4(),
        eventType: eventType,
        latencyMs: latencyMs,
        agentUsed: agentUsed,
        modelUsed: modelUsed,
        isOffline: isOffline,
        errorMessage: Value(errorMessage),
        createdAt: DateTime.now(),
      ));
    } catch (e) {
      debugPrint("[AuraAIRouter] Failed to log telemetry: $e");
    }
  }
}

final aiRouter = Provider<AuraAIRouter>((ref) {
  return AuraAIRouter(ref);
});
