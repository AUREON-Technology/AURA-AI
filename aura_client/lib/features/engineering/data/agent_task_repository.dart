import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/core/network/aura_api_error.dart';

/// One task as `GET /api/v3/agents/tasks` and `POST /api/v3/agents/tasks` return it (ADR-043).
class AgentTask {
  const AgentTask({
    required this.id,
    required this.goal,
    required this.persona,
    required this.status,
    required this.createdAt,
    this.queuePosition,
    this.errorMessage,
    this.runId,
  });

  final String id;
  final String goal;
  final String persona;

  /// queued | running | awaiting_approval | succeeded | failed | cancelled; an unknown status is
  /// kept as the server sent it.
  final String status;
  final DateTime createdAt;
  final int? queuePosition;
  final String? errorMessage;
  final String? runId;

  bool get isFailed => status == 'failed';

  factory AgentTask.fromJson(Map<String, dynamic> json, {Map<String, dynamic>? queue}) {
    final error = json['error'];
    final position = queue?['position'];
    return AgentTask(
      id: json['id']?.toString() ?? '',
      goal: json['goal']?.toString() ?? '',
      persona: json['persona']?.toString() ?? '',
      status: json['status']?.toString() ?? 'queued',
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? '') ?? DateTime.now(),
      queuePosition: position is int ? position : null,
      errorMessage: error is Map ? error['message']?.toString() : null,
      runId: json['run_id']?.toString(),
    );
  }
}

/// What the server said to a submission that it accepted.
class AgentTaskAccepted {
  const AgentTaskAccepted({required this.task, required this.idempotentReplay});

  final AgentTask task;

  /// True when the server had already accepted this `client_request_id` (HTTP 200): the earlier
  /// attempt got through and only its answer was lost, so nothing ran a second time.
  final bool idempotentReplay;
}

Map<String, dynamic> _asMap(Object? value) =>
    value is Map ? value.map((k, v) => MapEntry(k.toString(), v)) : <String, dynamic>{};

/// Thin client for the agent task gateway (`/api/v3/agents/tasks`, ADR-043).
class AgentTaskRepository {
  AgentTaskRepository(this._api);

  final AuraApiClient _api;

  /// `POST /agents/tasks` with [clientRequestId] as the body field *and* the `Idempotency-Key`
  /// header (the server requires them to agree). The same id on every replay is what makes a
  /// double submit impossible: a repeat answers 200 with the same task instead of running again.
  ///
  /// Throws [AuraApiError] for every refusal (the caller decides what a 4xx versus a 5xx means)
  /// and for an unreachable server.
  Future<AgentTaskAccepted> submit({
    required String goal,
    required String clientRequestId,
    String? persona,
    String? priority,
  }) async {
    final response = await _api.request(
      method: 'POST',
      path: '/api/v3/agents/tasks',
      idempotencyKey: clientRequestId,
      body: {
        'goal': goal,
        'persona': ?persona,
        'priority': ?priority,
        'client_request_id': clientRequestId,
      },
    );
    if (response.statusCode != 200 && response.statusCode != 202) {
      throw AuraApiError.fromResponse(
        statusCode: response.statusCode,
        body: response.body,
        retryAfterHeader: response.headers['retry-after'],
        correlationId: response.headers['x-correlation-id'],
      );
    }
    Map<String, dynamic> map;
    try {
      map = _asMap(jsonDecode(response.body));
    } catch (_) {
      map = <String, dynamic>{};
    }
    return AgentTaskAccepted(
      task: AgentTask.fromJson(_asMap(map['task']), queue: _asMap(map['queue'])),
      idempotentReplay: map['idempotent_replay'] == true || response.statusCode == 200,
    );
  }

  /// `GET /agents/tasks`: the caller's own tasks, newest first.
  Future<List<AgentTask>> list({int limit = 20}) async {
    final decoded = await _api.json(method: 'GET', path: '/api/v3/agents/tasks?limit=$limit');
    final rows = decoded['tasks'];
    return (rows is List ? rows : const []).whereType<Map>().map((r) => AgentTask.fromJson(_asMap(r))).toList();
  }
}

final agentTaskRepositoryProvider = Provider<AgentTaskRepository>(
  (ref) => AgentTaskRepository(ref.read(auraApiClientProvider)),
);

/// Recent tasks the server holds for this person; `autoDispose` so the screen always reads fresh.
final agentTasksProvider = FutureProvider.autoDispose<List<AgentTask>>(
  (ref) => ref.read(agentTaskRepositoryProvider).list(),
);
