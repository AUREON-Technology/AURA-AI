import 'dart:convert';

/// Persisted extras from a streamed v3 turn (`done` + `step` + `revision`).
class TurnMeta {
  final String? turnId;
  final String? correlationId;
  final double? confidence;
  final bool degraded;
  final bool needsConfirmation;
  final bool tainted;
  final bool guardsComplete;
  final bool revised;
  final bool searchedButEmpty;
  final Map<String, dynamic>? usage;
  final List<TurnStep> steps;
  final List<PendingToolCall> pendingToolCalls;
  final List<PlanStep> plan;
  // The persona id the backend actually used for this turn. May differ from the persona the
  // client requested if the caller lacked that persona's required capability and
  // AgentRegistry.resolve() silently fell back to the orchestrator -- absent (null) against an
  // un-upgraded backend that doesn't send this field yet.
  final String? resolvedAgent;

  const TurnMeta({
    this.turnId,
    this.correlationId,
    this.confidence,
    this.degraded = false,
    this.needsConfirmation = false,
    this.tainted = false,
    this.guardsComplete = false,
    this.revised = false,
    this.searchedButEmpty = false,
    this.usage,
    this.steps = const [],
    this.pendingToolCalls = const [],
    this.plan = const [],
    this.resolvedAgent,
  });

  bool get showLowConfidence => confidence != null && confidence! < 0.45;

  TurnMeta copyWith({
    String? turnId,
    String? correlationId,
    double? confidence,
    bool? degraded,
    bool? needsConfirmation,
    bool? tainted,
    bool? guardsComplete,
    bool? revised,
    bool? searchedButEmpty,
    Map<String, dynamic>? usage,
    List<TurnStep>? steps,
    List<PendingToolCall>? pendingToolCalls,
    List<PlanStep>? plan,
    String? resolvedAgent,
  }) {
    return TurnMeta(
      turnId: turnId ?? this.turnId,
      correlationId: correlationId ?? this.correlationId,
      confidence: confidence ?? this.confidence,
      degraded: degraded ?? this.degraded,
      needsConfirmation: needsConfirmation ?? this.needsConfirmation,
      tainted: tainted ?? this.tainted,
      guardsComplete: guardsComplete ?? this.guardsComplete,
      revised: revised ?? this.revised,
      searchedButEmpty: searchedButEmpty ?? this.searchedButEmpty,
      usage: usage ?? this.usage,
      steps: steps ?? this.steps,
      pendingToolCalls: pendingToolCalls ?? this.pendingToolCalls,
      plan: plan ?? this.plan,
      resolvedAgent: resolvedAgent ?? this.resolvedAgent,
    );
  }

  Map<String, dynamic> toJson() => {
        'turn_id': turnId,
        'correlation_id': correlationId,
        'confidence': confidence,
        'degraded': degraded,
        'needs_confirmation': needsConfirmation,
        'tainted': tainted,
        'guards_complete': guardsComplete,
        'revised': revised,
        'searched_but_empty': searchedButEmpty,
        'usage': usage,
        'steps': steps.map((s) => s.toJson()).toList(),
        'pending_tool_calls': pendingToolCalls.map((p) => p.toJson()).toList(),
        'plan': plan.map((p) => p.toJson()).toList(),
        'resolved_agent': resolvedAgent,
      };

  String encode() => jsonEncode(toJson());

  factory TurnMeta.fromJson(Map<String, dynamic> json) {
    final rawSteps = json['steps'];
    final rawPlan = json['plan'];
    return TurnMeta(
      turnId: json['turn_id']?.toString(),
      correlationId: json['correlation_id']?.toString(),
      confidence: _asDouble(json['confidence']),
      degraded: json['degraded'] == true,
      needsConfirmation: _readNeedsConfirmation(json['needs_confirmation']),
      tainted: json['tainted'] == true,
      guardsComplete: json['guards_complete'] == true,
      revised: json['revised'] == true,
      searchedButEmpty: json['searched_but_empty'] == true,
      usage: json['usage'] is Map<String, dynamic>
          ? json['usage'] as Map<String, dynamic>
          : null,
      steps: rawSteps is List
          ? rawSteps
              .whereType<Map>()
              .map((s) => TurnStep.fromJson(Map<String, dynamic>.from(s)))
              .toList()
          : const [],
      pendingToolCalls: json['pending_tool_calls'] is List
          ? (json['pending_tool_calls'] as List)
              .whereType<Map>()
              .map((p) => PendingToolCall.fromJson(Map<String, dynamic>.from(p)))
              .toList()
          : const [],
      plan: rawPlan is List
          ? rawPlan
              .whereType<Map>()
              .map((p) => PlanStep.fromJson(Map<String, dynamic>.from(p)))
              .toList()
          : const [],
      resolvedAgent: json['resolved_agent']?.toString(),
    );
  }

  factory TurnMeta.decode(String? raw) {
    if (raw == null || raw.isEmpty) return const TurnMeta();
    try {
      final decoded = jsonDecode(raw);
      if (decoded is Map<String, dynamic>) return TurnMeta.fromJson(decoded);
      if (decoded is Map) {
        return TurnMeta.fromJson(decoded.map((k, v) => MapEntry(k.toString(), v)));
      }
    } catch (_) {}
    return const TurnMeta();
  }

  static double? _asDouble(Object? value) {
    if (value is num) return value.toDouble();
    return null;
  }

  static bool _readNeedsConfirmation(Object? raw) {
    if (raw == true) return true;
    if (raw is List) return raw.isNotEmpty;
    return false;
  }
}

class TurnStep {
  final String? stepId;
  final String? kind;
  final String? status;
  final String? label;
  final String? detail;
  final double? elapsedMs;

  const TurnStep({
    this.stepId,
    this.kind,
    this.status,
    this.label,
    this.detail,
    this.elapsedMs,
  });

  Map<String, dynamic> toJson() => {
        'step_id': stepId,
        'kind': kind,
        'status': status,
        'label': label,
        'detail': detail,
        'elapsed_ms': elapsedMs,
      };

  factory TurnStep.fromJson(Map<String, dynamic> json) {
    return TurnStep(
      stepId: (json['step_id'] ?? json['id'])?.toString(),
      kind: json['kind']?.toString(),
      status: json['status']?.toString(),
      // The backend's `StepEvent.as_dict()` sends the step's text under `description` -- this
      // used to be dropped entirely (only `label`/`name`/`kind` were read), which is why steps
      // were never worth rendering. `label` here is kept for backward compatibility with any
      // already-persisted `TurnStep` JSON that only ever had that key.
      label: (json['description'] ?? json['label'] ?? json['name'] ?? json['kind'])?.toString(),
      detail: json['detail']?.toString(),
      elapsedMs: (json['elapsed_ms'] as num?)?.toDouble(),
    );
  }
}

/// One entry from the backend's whole-turn `plan_summary` -- the plan as composed, not the live
/// per-step progress `TurnStep` already covers. Distinct from `TurnStep`: this reflects the plan
/// at the moment execution finished (final `status` per step), not a chronological event stream.
class PlanStep {
  final String? id;
  final String? kind;
  final String? status;
  final String? description;

  const PlanStep({this.id, this.kind, this.status, this.description});

  Map<String, dynamic> toJson() => {
        'id': id,
        'kind': kind,
        'status': status,
        'description': description,
      };

  factory PlanStep.fromJson(Map<String, dynamic> json) {
    return PlanStep(
      id: json['id']?.toString(),
      kind: json['kind']?.toString(),
      status: json['status']?.toString(),
      description: json['description']?.toString(),
    );
  }
}

/// One tool call the backend is holding pending the user's explicit confirmation
/// (`TurnResult.pending_tool_calls` / the `done` SSE frame's `pending_tool_calls`) -- carries the
/// call's actual arguments, not just its tool name, so the client can show what it's approving.
class PendingToolCall {
  final String? callId;
  final String tool;
  final Map<String, dynamic> arguments;
  // Which turn produced this pending call -- lets the confirmation UI scope itself to the
  // specific assistant message it belongs to instead of a single global list with no owner.
  // Populated client-side from the turn that received it (the backend's `pending_tool_calls`
  // entries don't carry their own turn id -- they're already inside that turn's `done` frame).
  final String? turnId;
  // A real unified diff for this call, when the backend could produce one (currently only for
  // `github_commit_files`/`git_commit` confirmations with an active sandbox session -- see
  // `AIKernel._record_engineer_runs`'s sibling diff-attachment logic). Null means "no diff
  // available", not "no changes" -- callers should fall back to a content-only preview.
  final String? diffPreview;

  const PendingToolCall({
    this.callId,
    required this.tool,
    this.arguments = const {},
    this.turnId,
    this.diffPreview,
  });

  PendingToolCall copyWith({String? turnId}) => PendingToolCall(
        callId: callId,
        tool: tool,
        arguments: arguments,
        turnId: turnId ?? this.turnId,
        diffPreview: diffPreview,
      );

  Map<String, dynamic> toJson() => {
        'call_id': callId,
        'tool': tool,
        'arguments': arguments,
        'turn_id': turnId,
        'diff_preview': diffPreview,
      };

  factory PendingToolCall.fromJson(Map<String, dynamic> json) {
    return PendingToolCall(
      callId: json['call_id']?.toString(),
      tool: json['tool']?.toString() ?? '',
      arguments: json['arguments'] is Map
          ? Map<String, dynamic>.from(json['arguments'] as Map)
          : const {},
      turnId: json['turn_id']?.toString(),
      diffPreview: json['diff_preview']?.toString(),
    );
  }
}
