import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/network/aura_api_client.dart';

Map<String, dynamic> _asMap(Object? value) =>
    value is Map ? value.map((k, v) => MapEntry(k.toString(), v)) : <String, dynamic>{};

/// Totals for one bucket (the whole period, or one day / agent / task) of
/// `GET /api/v3/usage/summary` (ADR-043 section 6).
class UsageTotals {
  const UsageTotals({
    required this.turns,
    required this.failedTurns,
    required this.tokensIn,
    required this.tokensOut,
    required this.costUsd,
    required this.elapsedSeconds,
    this.key,
  });

  /// The group's day (`2026-11-20`), agent persona or task id; `(none)` for turns outside a task.
  final String? key;
  final int turns;
  final int failedTurns;
  final int tokensIn;
  final int tokensOut;
  final double costUsd;
  final double elapsedSeconds;

  int get totalTokens => tokensIn + tokensOut;

  factory UsageTotals.fromJson(Map<String, dynamic> json) {
    int asInt(Object? v) => v is num ? v.toInt() : 0;
    double asDouble(Object? v) => v is num ? v.toDouble() : 0;
    return UsageTotals(
      key: json['key']?.toString(),
      turns: asInt(json['turns']),
      failedTurns: asInt(json['failed_turns']),
      tokensIn: asInt(json['tokens_in']),
      tokensOut: asInt(json['tokens_out']),
      costUsd: asDouble(json['cost_usd']),
      elapsedSeconds: asDouble(json['elapsed_s']),
    );
  }
}

class UsageSummary {
  const UsageSummary({
    required this.enabled,
    required this.period,
    required this.groupBy,
    this.totals,
    this.groups = const [],
    this.truncated = false,
    this.note,
  });

  /// False when the deployment records no usage; [note] says so.
  final bool enabled;
  final String period;
  final String groupBy;
  final UsageTotals? totals;
  final List<UsageTotals> groups;

  /// The server's window did not reach back to the start of the period: totals are a floor.
  final bool truncated;
  final String? note;
}

class UsageTurn {
  const UsageTurn({
    required this.turnId,
    required this.at,
    required this.agent,
    required this.outcome,
    required this.promptTokens,
    required this.completionTokens,
    required this.costUsd,
    required this.elapsedMs,
    this.provider,
    this.model,
    this.taskId,
    this.errorKind,
    this.cached = false,
  });

  final String turnId;
  final DateTime? at;
  final String agent;
  final String outcome;
  final String? errorKind;
  final String? provider;
  final String? model;
  final String? taskId;
  final bool cached;
  final int promptTokens;
  final int completionTokens;
  final double costUsd;
  final double elapsedMs;

  bool get succeeded => outcome == 'success';

  factory UsageTurn.fromJson(Map<String, dynamic> json) {
    final usage = _asMap(json['usage']);
    int asInt(Object? v) => v is num ? v.toInt() : 0;
    double asDouble(Object? v) => v is num ? v.toDouble() : 0;
    return UsageTurn(
      turnId: json['turn_id']?.toString() ?? '',
      at: DateTime.tryParse(json['at']?.toString() ?? ''),
      agent: json['agent']?.toString() ?? '',
      outcome: json['outcome']?.toString() ?? '',
      errorKind: json['error_kind']?.toString(),
      provider: json['provider']?.toString(),
      model: json['model']?.toString(),
      taskId: json['task_id']?.toString(),
      cached: json['cached'] == true,
      promptTokens: asInt(usage['prompt_tokens']),
      completionTokens: asInt(usage['completion_tokens']),
      costUsd: asDouble(usage['cost_usd']),
      elapsedMs: asDouble(usage['elapsed_ms']),
    );
  }
}

const usagePeriods = ['day', 'week', 'month'];
const usageGroupings = ['day', 'agent', 'task'];

class UsageRepository {
  UsageRepository(this._api);

  final AuraApiClient _api;

  /// `GET /usage/summary?period=&group_by=`.
  Future<UsageSummary> summary({required String period, required String groupBy}) async {
    final decoded = await _api.json(
      method: 'GET',
      path: '/api/v3/usage/summary?period=$period&group_by=$groupBy',
    );
    final enabled = decoded['enabled'] != false;
    final totals = decoded['totals'];
    final groups = decoded['groups'];
    return UsageSummary(
      enabled: enabled,
      period: decoded['period']?.toString() ?? period,
      groupBy: decoded['group_by']?.toString() ?? groupBy,
      totals: totals is Map ? UsageTotals.fromJson(_asMap(totals)) : null,
      groups: (groups is List ? groups : const []).whereType<Map>().map((g) => UsageTotals.fromJson(_asMap(g))).toList(),
      truncated: decoded['truncated'] == true,
      note: decoded['note']?.toString(),
    );
  }

  /// `GET /usage/turns?limit=`: newest first.
  Future<List<UsageTurn>> turns({int limit = 20}) async {
    final decoded = await _api.json(method: 'GET', path: '/api/v3/usage/turns?limit=$limit');
    final rows = decoded['turns'];
    return (rows is List ? rows : const []).whereType<Map>().map((t) => UsageTurn.fromJson(_asMap(t))).toList();
  }
}

final usageRepositoryProvider = Provider<UsageRepository>(
  (ref) => UsageRepository(ref.read(auraApiClientProvider)),
);

/// (period, group_by) as the screen's selection.
typedef UsageQuery = ({String period, String groupBy});

final usageSummaryProvider = FutureProvider.autoDispose.family<UsageSummary, UsageQuery>(
  (ref, q) => ref.read(usageRepositoryProvider).summary(period: q.period, groupBy: q.groupBy),
);

final usageTurnsProvider = FutureProvider.autoDispose<List<UsageTurn>>(
  (ref) => ref.read(usageRepositoryProvider).turns(),
);
