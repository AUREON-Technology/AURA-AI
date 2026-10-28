/// Picks outbox row ids that the server finished with, matching [results]
/// to [pendingIds] by index.
///
/// The sync API echoes the *record* UUID as `id`, not the outbox row id, so
/// index is the only stable join. Rows whose status is `error` (unknown
/// table, bad UUID, etc.) are left in the queue for retry — HTTP 200 used
/// to wipe the entire outbox anyway.
List<String> outboxIdsToDrop({
  required List<String> pendingIds,
  required List<dynamic> results,
}) =>
    classifyOutboxResults(pendingIds: pendingIds, results: results).drop;

/// One outbox row the server refused.
class OutboxFailure {
  final String outboxId;
  final String? detail;

  /// False when the server says resending the same mutation will fail the
  /// same way (bad value, unknown table, id not a UUID): the row is parked at
  /// once instead of being retried [kMaxOutboxAttempts] times. A server that
  /// predates the flag sends none, which counts as retryable.
  final bool retryable;

  const OutboxFailure({required this.outboxId, this.detail, required this.retryable});
}

/// What to do with each outbox row after one push.
class OutboxAck {
  /// Rows the server is done with: `applied`, or `conflict` (the server kept
  /// its own version).
  final List<String> drop;

  /// Rows that came back `error`.
  final List<OutboxFailure> failures;

  /// Server rows carried by `conflict` and natural-key-merge results, by
  /// table -- the same shape as a pull's `data`, so they apply the same way.
  /// Applying them is what makes a conflict converge: the next pull resumes
  /// from a cursor the unchanged server row may already be behind.
  final Map<String, List<Map<String, dynamic>>> serverRows;

  const OutboxAck({required this.drop, required this.failures, required this.serverRows});
}

OutboxAck classifyOutboxResults({
  required List<String> pendingIds,
  required List<dynamic> results,
}) {
  final drop = <String>[];
  final failures = <OutboxFailure>[];
  final serverRows = <String, List<Map<String, dynamic>>>{};
  for (var i = 0; i < pendingIds.length && i < results.length; i++) {
    final item = results[i];
    if (item is! Map) continue;
    final status = item['status'] as String?;
    final row = item['row'];
    final table = item['table'];
    if (row is Map<String, dynamic> && table is String) {
      serverRows.putIfAbsent(table, () => []).add(row);
    }
    if (status == 'applied' || status == 'conflict') {
      drop.add(pendingIds[i]);
    } else if (status == 'error') {
      failures.add(OutboxFailure(
        outboxId: pendingIds[i],
        detail: item['detail'] as String?,
        retryable: item['retryable'] != false,
      ));
    }
  }
  return OutboxAck(drop: drop, failures: failures, serverRows: serverRows);
}
