import 'dart:convert';

/// Persisted extras from a streamed v3 turn (`done` + `step` + `revision`).
class TurnMeta {
  final String? turnId;
  final String? correlationId;
  final double? confidence;
  final bool degraded;
  // Why [degraded] is true: the backend's bounded slugs (`planning_failed`, `tool_failure`,
  // `unbacked_action_claim`, ...), so the row can say what went wrong instead of one generic
  // "reduced mode" line (audit C3). Empty against a backend that does not send them.
  final List<String> degradedReasons;
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
  // Client-side only: the stream ended before `done` -- the user pressed Stop, the connection
  // dropped, or it went idle -- and [Message.content] holds only the partial answer (F-31).
  final bool interrupted;
  // Client-side only (G-14a): the user answered this turn's held calls (approved or declined),
  // so they are not offered again after a restart and the bubble no longer asks to confirm.
  final bool confirmationResolved;
  // Client-side only (G-14a, WP4): where on the user's computer the turn ran, and whether in its
  // own worktree -- so approving its held calls after a restart resumes it in the same place.
  final String? workspaceRoot;
  final bool desktopTaskIsolation;

  const TurnMeta({
    this.turnId,
    this.correlationId,
    this.confidence,
    this.degraded = false,
    this.degradedReasons = const [],
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
    this.interrupted = false,
    this.confirmationResolved = false,
    this.workspaceRoot,
    this.desktopTaskIsolation = false,
  });

  bool get showLowConfidence => confidence != null && confidence! < 0.45;

  TurnMeta copyWith({
    String? turnId,
    String? correlationId,
    double? confidence,
    bool? degraded,
    List<String>? degradedReasons,
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
    bool? interrupted,
    bool? confirmationResolved,
    String? workspaceRoot,
    bool? desktopTaskIsolation,
  }) {
    return TurnMeta(
      turnId: turnId ?? this.turnId,
      correlationId: correlationId ?? this.correlationId,
      confidence: confidence ?? this.confidence,
      degraded: degraded ?? this.degraded,
      degradedReasons: degradedReasons ?? this.degradedReasons,
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
      interrupted: interrupted ?? this.interrupted,
      confirmationResolved: confirmationResolved ?? this.confirmationResolved,
      workspaceRoot: workspaceRoot ?? this.workspaceRoot,
      desktopTaskIsolation: desktopTaskIsolation ?? this.desktopTaskIsolation,
    );
  }

  Map<String, dynamic> toJson() => {
        'turn_id': turnId,
        'correlation_id': correlationId,
        'confidence': confidence,
        'degraded': degraded,
        if (degradedReasons.isNotEmpty) 'degraded_reasons': degradedReasons,
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
        'interrupted': interrupted,
        if (confirmationResolved) 'confirmation_resolved': true,
        'workspace_root': ?workspaceRoot,
        if (desktopTaskIsolation) 'desktop_task_isolation': true,
      };

  String encode() => jsonEncode(toJson());

  factory TurnMeta.fromJson(Map<String, dynamic> json) {
    final rawSteps = json['steps'];
    // The two transports disagree on this key: the streamed `done` frame sends
    // `plan_summary` (kernel.py), while the non-streaming `/chat` body sends
    // `plan` (v3.py). Reading only `plan` meant the plan never arrived on the
    // streaming path -- which is the path chat actually uses -- so `plan` was
    // empty in practice for every real turn. Accepting both keeps this working
    // against either transport and against a backend older than this fix.
    final rawPlan = json['plan'] ?? json['plan_summary'];
    return TurnMeta(
      turnId: json['turn_id']?.toString(),
      correlationId: json['correlation_id']?.toString(),
      confidence: _asDouble(json['confidence']),
      degraded: json['degraded'] == true,
      degradedReasons: json['degraded_reasons'] is List
          ? (json['degraded_reasons'] as List).whereType<String>().toList()
          : const [],
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
      interrupted: json['interrupted'] == true,
      confirmationResolved: json['confirmation_resolved'] == true,
      workspaceRoot: json['workspace_root']?.toString(),
      desktopTaskIsolation: json['desktop_task_isolation'] == true,
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

/// A step the turn ended without reporting a final status for (G-14d): shown without a spinner.
const kStepUnfinished = 'unfinished';

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

  /// The same step with another status.
  TurnStep withStatus(String status) => TurnStep(
        stepId: stepId,
        kind: kind,
        status: status,
        label: label,
        detail: detail,
        elapsedMs: elapsedMs,
      );

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

/// One advisory risk flag the backend attaches to a held call (`confirmation_summary.py`).
/// [level] is `high`, `medium` or `low`; [code] names the kind (`command`, `sensitive_file`,
/// `migration`, `pipeline`, `deployment`, `remote_write`, `large_change`); [text] is the backend's
/// own English sentence. Advisory only: a call without flags is not thereby safe.
class RiskFlag {
  final String code;
  final String level;
  final String text;

  const RiskFlag({required this.code, required this.level, required this.text});

  Map<String, dynamic> toJson() => {'code': code, 'level': level, 'text': text};

  factory RiskFlag.fromJson(Map<String, dynamic> json) => RiskFlag(
        code: json['code']?.toString() ?? '',
        level: json['level']?.toString() ?? 'low',
        text: json['text']?.toString() ?? '',
      );
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
  // The server-issued, opaque id that approves exactly this call (F-04): the backend stores the
  // held turn and runs only a call identical to this one, once, when this id comes back in
  // `approval_ids`. A call without one (an older backend) cannot be confirmed from here.
  final String? approvalId;
  // A one-line plain description of the call ("Run: npm test") and its advisory risk flags, made by
  // the backend so a phone-sized card can lead with what is being approved instead of the raw command
  // or diff. Both are absent from an older backend: the card then shows the raw detail as before.
  final String? headline;
  final List<RiskFlag> riskFlags;

  const PendingToolCall({
    this.callId,
    required this.tool,
    this.arguments = const {},
    this.turnId,
    this.diffPreview,
    this.approvalId,
    this.headline,
    this.riskFlags = const [],
  });

  bool get canConfirm => approvalId != null && approvalId!.isNotEmpty;

  PendingToolCall copyWith({String? turnId}) => PendingToolCall(
        callId: callId,
        tool: tool,
        arguments: arguments,
        turnId: turnId ?? this.turnId,
        diffPreview: diffPreview,
        approvalId: approvalId,
        headline: headline,
        riskFlags: riskFlags,
      );

  Map<String, dynamic> toJson() => {
        'call_id': callId,
        'tool': tool,
        'arguments': arguments,
        'turn_id': turnId,
        'diff_preview': diffPreview,
        'approval_id': approvalId,
        'headline': headline,
        'risk_flags': riskFlags.map((f) => f.toJson()).toList(),
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
      approvalId: json['approval_id']?.toString(),
      headline: json['headline']?.toString(),
      riskFlags: json['risk_flags'] is List
          ? (json['risk_flags'] as List)
              .whereType<Map>()
              .map((f) => RiskFlag.fromJson(Map<String, dynamic>.from(f)))
              .toList()
          : const [],
    );
  }
}

/// Records a streamed step: a frame for a step already shown replaces it where it stands.
///
/// The server sends one frame when a step starts (`running`) and another when it ends
/// (`succeeded`, `done`, `failed`, `degraded`, ...) under the same `step_id`. Appending both left
/// every finished step's "running" row spinning forever beside its result. A step without an id
/// cannot be matched and is appended, as before.
void upsertStep(List<TurnStep> steps, TurnStep step) {
  final id = step.stepId;
  if (id != null) {
    final index = steps.indexWhere((s) => s.stepId == id);
    if (index >= 0) {
      steps[index] = step;
      return;
    }
  }
  steps.add(step);
}
