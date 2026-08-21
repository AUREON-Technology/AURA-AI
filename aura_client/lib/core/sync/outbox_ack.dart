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
}) {
  final drop = <String>[];
  for (var i = 0; i < pendingIds.length && i < results.length; i++) {
    final item = results[i];
    if (item is! Map) continue;
    final status = item['status'] as String?;
    if (status == 'applied' || status == 'conflict') {
      drop.add(pendingIds[i]);
    }
  }
  return drop;
}
