import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/network/aura_api_client.dart';

Map<String, dynamic> _asMap(Object? value) =>
    value is Map ? value.map((k, v) => MapEntry(k.toString(), v)) : <String, dynamic>{};

/// One agent run from `GET /api/v3/agents/runs/search` or `GET /api/v3/agents/runs/{id}`
/// (ADR-043 section 5). Distinct from [EngineerRun], which is one engineer *tool call*; this is a
/// whole run of an agent with its plan and tool counts.
class AgentRunSummary {
  const AgentRunSummary({
    required this.id,
    required this.agent,
    required this.status,
    required this.createdAt,
    required this.elapsedSeconds,
    required this.iterations,
    required this.planStepsTotal,
    required this.planStepsSucceeded,
    required this.toolCalls,
    required this.toolCallsOk,
    required this.tokensUsed,
    this.stopReason,
    this.goalPreview,
    this.tools = const [],
    this.matchedOn = const [],
  });

  final String id;
  final String agent;

  /// running | completed | partial | failed | awaiting_approval | budget_exhausted | ... (an
  /// unknown status is shown as sent).
  final String status;
  final DateTime createdAt;
  final double elapsedSeconds;
  final int iterations;
  final int planStepsTotal;
  final int planStepsSucceeded;
  final int toolCalls;
  final int toolCallsOk;
  final int tokensUsed;
  final String? stopReason;

  /// Only present when the operator turned previews on; otherwise goal text is not stored.
  final String? goalPreview;
  final List<String> tools;
  final List<String> matchedOn;

  /// A run that did not do what it set out to. Rendered red *and* with words.
  bool get isFailed => status == 'failed' || status == 'budget_exhausted';

  factory AgentRunSummary.fromJson(Map<String, dynamic> json) {
    int asInt(Object? v) => v is num ? v.toInt() : 0;
    final preview = json['goal_preview']?.toString();
    return AgentRunSummary(
      id: json['id']?.toString() ?? '',
      agent: (json['agent_id'] ?? json['agent'])?.toString() ?? '',
      status: json['status']?.toString() ?? '',
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? '') ?? DateTime.now(),
      elapsedSeconds: json['elapsed_s'] is num ? (json['elapsed_s'] as num).toDouble() : 0,
      iterations: asInt(json['iterations']),
      planStepsTotal: asInt(json['plan_steps_total'] ?? json['plan_steps']),
      planStepsSucceeded: asInt(json['plan_steps_succeeded']),
      toolCalls: asInt(json['tool_calls']),
      toolCallsOk: asInt(json['tool_calls_ok']),
      tokensUsed: asInt(json['tokens_used']),
      stopReason: json['stop_reason']?.toString(),
      goalPreview: preview == null || preview.isEmpty ? null : preview,
      tools: (json['tools'] is List ? json['tools'] as List : const []).map((t) => t.toString()).toList(),
      matchedOn: (json['matched_on'] is List ? json['matched_on'] as List : const []).map((t) => t.toString()).toList(),
    );
  }
}

/// One step of a run: the timeline entry (`steps[]` of `GET /agents/runs/{id}`).
class AgentRunStep {
  const AgentRunStep({
    required this.seq,
    required this.iteration,
    required this.phase,
    required this.kind,
    required this.status,
    required this.createdAt,
    this.tool,
    this.errorKind,
  });

  final int seq;
  final int iteration;
  final String phase;
  final String kind;
  final String status;
  final DateTime createdAt;
  final String? tool;
  final String? errorKind;

  bool get isFailed => status == 'failed' || status == 'error' || errorKind != null;

  factory AgentRunStep.fromJson(Map<String, dynamic> json) {
    int asInt(Object? v) => v is num ? v.toInt() : 0;
    final tool = json['tool']?.toString();
    final errorKind = json['error_kind']?.toString();
    return AgentRunStep(
      seq: asInt(json['seq']),
      iteration: asInt(json['iteration']),
      phase: json['phase']?.toString() ?? '',
      kind: json['kind']?.toString() ?? '',
      status: json['status']?.toString() ?? '',
      createdAt: DateTime.tryParse(json['created_at']?.toString() ?? '') ?? DateTime.now(),
      tool: tool == null || tool.isEmpty ? null : tool,
      errorKind: errorKind == null || errorKind.isEmpty ? null : errorKind,
    );
  }
}

/// A run with its steps, in order (`GET /agents/runs/{id}`).
class AgentRunTimeline {
  const AgentRunTimeline({required this.run, required this.steps});

  final AgentRunSummary run;
  final List<AgentRunStep> steps;
}

class RunSearchResult {
  const RunSearchResult({
    required this.query,
    required this.totalMatches,
    required this.runs,
    required this.goalTextSearchable,
    this.scopeNote,
  });

  final String query;
  final int totalMatches;
  final List<AgentRunSummary> runs;

  /// False unless the operator stores goal previews: the screen says what is being searched.
  final bool goalTextSearchable;
  final String? scopeNote;
}

/// What `POST /api/v3/agents/stop-all` reports (ADR-041): how many of the caller's turns it cancelled
/// and how many paired-desktop sessions it closed. [scope] is `this_process`: a turn held by another
/// gateway worker is not reached.
class StopAllResult {
  const StopAllResult({required this.turnsStopped, required this.desktopSessionsClosed, required this.scope});

  final int turnsStopped;
  final int desktopSessionsClosed;
  final String scope;

  bool get stoppedAnything => turnsStopped > 0 || desktopSessionsClosed > 0;

  factory StopAllResult.fromJson(Map<String, dynamic> json) => StopAllResult(
        turnsStopped: json['turns_stopped'] is num ? (json['turns_stopped'] as num).toInt() : 0,
        desktopSessionsClosed:
            json['desktop_sessions_closed'] is num ? (json['desktop_sessions_closed'] as num).toInt() : 0,
        scope: json['scope']?.toString() ?? '',
      );
}

class RunHistoryRepository {
  RunHistoryRepository(this._api);

  final AuraApiClient _api;

  /// `POST /agents/stop-all`: stop everything the person's agent is doing, now. Idempotent on the
  /// server (nothing running answers zeros), so it is safe to press twice. Never revokes devices from
  /// here: that is a separate, deliberate action.
  Future<StopAllResult> stopAll() async {
    final decoded = await _api.json(method: 'POST', path: '/api/v3/agents/stop-all', body: const {});
    return StopAllResult.fromJson(decoded);
  }

  /// `POST /agents/pause`: hold running turns at their next checkpoint (ADR-059). Returns how many.
  Future<int> pause() async {
    final decoded = await _api.json(method: 'POST', path: '/api/v3/agents/pause', body: const {});
    return decoded['turns_paused'] is num ? (decoded['turns_paused'] as num).toInt() : 0;
  }

  /// `POST /agents/resume`: let paused turns carry on. Returns how many.
  Future<int> resume() async {
    final decoded = await _api.json(method: 'POST', path: '/api/v3/agents/resume', body: const {});
    return decoded['turns_resumed'] is num ? (decoded['turns_resumed'] as num).toInt() : 0;
  }

  /// `POST /agents/redirect`: send [instruction] to running turns; they replan with it at the next
  /// checkpoint. Returns how many turns it reached (0 when nothing is running here).
  Future<int> redirect(String instruction) async {
    final decoded = await _api.json(
      method: 'POST', path: '/api/v3/agents/redirect', body: {'instruction': instruction});
    return decoded['turns_redirected'] is num ? (decoded['turns_redirected'] as num).toInt() : 0;
  }

  /// `GET /agents/runs/search?q=&status=&limit=`. An empty [query] lists the newest runs.
  Future<RunSearchResult> search({String query = '', String? status, int limit = 30}) async {
    final params = <String, String>{
      if (query.trim().isNotEmpty) 'q': query.trim(),
      if (status != null && status.isNotEmpty) 'status': status,
      'limit': '$limit',
    };
    final decoded = await _api.json(
      method: 'GET',
      path: '/api/v3/agents/runs/search?${Uri(queryParameters: params).query}',
    );
    final scope = _asMap(decoded['search_scope']);
    final rows = decoded['runs'];
    return RunSearchResult(
      query: decoded['query']?.toString() ?? query,
      totalMatches: decoded['total_matches'] is num ? (decoded['total_matches'] as num).toInt() : 0,
      runs: (rows is List ? rows : const []).whereType<Map>().map((r) => AgentRunSummary.fromJson(_asMap(r))).toList(),
      goalTextSearchable: scope['goal_text_searchable'] == true,
      scopeNote: scope['note']?.toString(),
    );
  }

  /// `GET /agents/runs/{id}`: the run and its steps, oldest first.
  Future<AgentRunTimeline> timeline(String runId) async {
    final decoded = await _api.json(
      method: 'GET',
      path: '/api/v3/agents/runs/${Uri.encodeComponent(runId)}',
    );
    final steps = decoded['steps'];
    final parsed = (steps is List ? steps : const [])
        .whereType<Map>()
        .map((s) => AgentRunStep.fromJson(_asMap(s)))
        .toList()
      ..sort((a, b) => a.seq.compareTo(b.seq));
    return AgentRunTimeline(run: AgentRunSummary.fromJson(decoded), steps: parsed);
  }
}

final runHistoryRepositoryProvider = Provider<RunHistoryRepository>(
  (ref) => RunHistoryRepository(ref.read(auraApiClientProvider)),
);

/// The run timeline for one run id.
final agentRunTimelineProvider = FutureProvider.autoDispose.family<AgentRunTimeline, String>(
  (ref, runId) => ref.read(runHistoryRepositoryProvider).timeline(runId),
);
