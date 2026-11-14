/// `Idempotency-Key` for side-effecting requests (audit FSA-046).
///
/// The server has honoured the header on every write route since roadmap P1.4 (`aura_core/api/
/// idempotency.py`): a retry with the same key replays the first answer instead of writing again.
/// No client ever sent it, so a retried POST created a second memory and a retried DELETE got a
/// 404. Every write path here now sends one key per logical request -- the same key on the 401
/// re-send inside a client, and a *stable* key for an outbox delete that is retried across syncs,
/// so the server sees one operation however many times the network made us repeat it.
library;

import 'package:uuid/uuid.dart';

const idempotencyKeyHeader = 'Idempotency-Key';

const _uuid = Uuid();

/// A fresh key for one logical write.
String newIdempotencyKey() => _uuid.v4();

/// A key derived from what is being done to what, for a write that a durable queue replays
/// (printable ASCII only, which is all the server accepts).
String stableIdempotencyKey(String operation, String id) => '$operation-$id';

bool isSideEffecting(String method) {
  switch (method.toUpperCase()) {
    case 'GET':
    case 'HEAD':
    case 'OPTIONS':
      return false;
    default:
      return true;
  }
}
