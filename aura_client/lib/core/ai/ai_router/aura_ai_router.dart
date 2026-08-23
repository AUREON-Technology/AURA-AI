import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../../database/database.dart';
import '../../database/database_provider.dart';
import '../../network/aura_api_client.dart';

class AIRouterStatus {
  final String activeModel;
  final double latencyMs;
  final String status;
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

  Future<bool> checkConnectivity() async {
    try {
      final response = await _ref
          .read(auraApiClientProvider)
          .probeHealth()
          .timeout(const Duration(seconds: 2));
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  /// Probes `/health/ready` and updates [aiRouterStatusProvider].
  Future<bool> probeGateway() async {
    final stopwatch = Stopwatch()..start();
    final notifier = _ref.read(aiRouterStatusProvider.notifier);
    try {
      final response = await _ref.read(auraApiClientProvider).probeHealth();
      final ok = response.statusCode == 200;
      notifier.updateStatus(AIRouterStatus(
        activeModel: 'server',
        latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
        status: ok ? 'Online' : 'AURA unreachable',
        isOffline: !ok,
      ));
      return ok;
    } catch (e) {
      notifier.updateStatus(AIRouterStatus(
        activeModel: 'server',
        latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
        status: 'AURA unreachable',
        isOffline: true,
      ));
      await _logTelemetry(
        eventType: 'error',
        latencyMs: stopwatch.elapsedMilliseconds.toDouble(),
        agentUsed: 'health',
        modelUsed: 'server',
        isOffline: true,
        errorMessage: e.toString(),
      );
      return false;
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
      debugPrint('[AuraAIRouter] Failed to log telemetry: $e');
    }
  }
}

final aiRouter = Provider<AuraAIRouter>((ref) {
  return AuraAIRouter(ref);
});
