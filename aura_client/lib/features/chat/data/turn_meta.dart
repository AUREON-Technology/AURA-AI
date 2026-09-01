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
      };

  String encode() => jsonEncode(toJson());

  factory TurnMeta.fromJson(Map<String, dynamic> json) {
    final rawSteps = json['steps'];
    // The wire format (the backend's `done` frame) sends a list of pending tool names; the
    // locally persisted cache (TurnMeta.encode -> Message.turnMetaJson) round-trips it as the
    // plain bool this class exposes. Accept either so decoding a cached message doesn't lose it.
    final rawNeedsConfirmation = json['needs_confirmation'];
    final needsConfirmation = rawNeedsConfirmation is List
        ? rawNeedsConfirmation.isNotEmpty
        : rawNeedsConfirmation == true;
    return TurnMeta(
      turnId: json['turn_id']?.toString(),
      correlationId: json['correlation_id']?.toString(),
      confidence: _asDouble(json['confidence']),
      degraded: json['degraded'] == true,
      needsConfirmation: needsConfirmation,
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
}

class TurnStep {
  final String? stepId;
  final String? kind;
  final String? status;
  final String? label;

  const TurnStep({this.stepId, this.kind, this.status, this.label});

  Map<String, dynamic> toJson() => {
        'step_id': stepId,
        'kind': kind,
        'status': status,
        'label': label,
      };

  factory TurnStep.fromJson(Map<String, dynamic> json) {
    return TurnStep(
      stepId: (json['step_id'] ?? json['id'])?.toString(),
      kind: json['kind']?.toString(),
      status: json['status']?.toString(),
      label: (json['label'] ?? json['name'] ?? json['kind'])?.toString(),
    );
  }
}
