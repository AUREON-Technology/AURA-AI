import 'dart:convert';
import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../auth/token_manager.dart';
import '../config/backend_url_provider.dart';
import '../database/database.dart';
import '../database/database_provider.dart';
import '../database/health_day.dart';
import '../security/data_key_client.dart';
import '../security/data_key_migration.dart';
import '../security/device_validation_service.dart';
import '../security/encryption_service.dart';
import 'outbox_ack.dart';
import 'remote_deletes.dart';
import 'sync_result.dart';

/// V2 cursor-based sync client (Doc/V2_BLUEPRINT.md §5/§7) — talks to
/// `/api/v3/sync` on the FastAPI backend, which owns Postgres as the
/// relational source of truth. Replaces V1's `SyncEngine`, which wrote
/// directly to Firestore from the client and hand-rolled five nearly
/// identical last-write-wins comparators (577 lines). Here the server does
/// the diffing (`WHERE user_id = ? AND sync_txid >= ?`); the client only
/// has to (1) push its outbox and (2) apply whatever rows come back.
///
/// Scope note: Tasks/CalendarEvents/FinanceRecords/HealthRecords/Messages
/// are wired to this protocol. UserMemories go through `/api/v3/memory`
/// (MemoryRepository) — that data is shaped around embeddings, not a plain
/// CRUD row.
class SyncClient {
  final AppDatabase _db;
  final Ref _ref;
  final http.Client? _http;
  SyncClient(this._db, this._ref, {http.Client? httpClient}) : _http = httpClient;

  String get _syncUrl => '${_ref.read(backendUrlProvider)}/api/v3/sync';
  String get _backendUrl => _ref.read(backendUrlProvider);

  // v3 endpoints authenticate via TokenManager's backend-issued JWT
  // (Doc/V2_BLUEPRINT.md §9), not the raw Google ID token older endpoints used.
  Future<Map<String, String>?> _headers() async {
    final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
    if (token == null) return null;
    final headers = <String, String>{
      "Content-Type": "application/json",
      "Authorization": "Bearer $token",
    };
    headers.addAll(
      await DeviceValidationService.getAttestationHeaders(backendUrl: _backendUrl),
    );
    return headers;
  }

  /// What having no access token means for this attempt (INT-028).
  ///
  /// [TokenManager] keeps the refresh token when a renewal fails on the
  /// network and deletes it when the server refuses it, so a refresh token
  /// still on disk means "could not renew right now" -- offline, not signed
  /// out. Reporting that as `unauthorized` showed a sync error (and, for a
  /// push, skipped the pull) every time the device went offline with an
  /// expired access token.
  Future<SyncFailureKind> _noTokenFailure() async {
    try {
      final resumable = await _ref.read(tokenManagerProvider).hasCachedRefreshToken();
      return resumable ? SyncFailureKind.network : SyncFailureKind.unauthorized;
    } catch (_) {
      return SyncFailureKind.unauthorized;
    }
  }

  /// One 401 retry after dropping the cached access token. Returns null when
  /// there is no JWT so callers skip the HTTP round-trip (a 403 attestation
  /// failure cannot be retried into existence); [_noTokenFailure] says what
  /// that null means.
  Future<http.Response?> _send(
    Future<http.Response> Function(Map<String, String> headers) send,
  ) async {
    Future<http.Response?> once() async {
      final headers = await _headers();
      if (headers == null) return null;
      return send(headers);
    }

    var response = await once();
    if (response == null) return null;
    if (response.statusCode == 401) {
      debugPrint("[SyncClient] 401 — invalidating access token and retrying once");
      _ref.read(tokenManagerProvider).invalidateAccessToken();
      response = await once();
    }
    return response;
  }

  Future<http.Response> _post(Uri uri, {required Map<String, String> headers, required String body}) {
    final client = _http;
    final sent = client != null
        ? client.post(uri, headers: headers, body: body)
        : http.post(uri, headers: headers, body: body);
    return sent.timeout(const Duration(seconds: 20));
  }

  Future<http.Response> _get(Uri uri, {required Map<String, String> headers}) {
    final client = _http;
    final sent = client != null ? client.get(uri, headers: headers) : http.get(uri, headers: headers);
    return sent.timeout(const Duration(seconds: 20));
  }

  /// Pushes the outbox, then pulls everything changed since the last
  /// cursor. Distinguishes 401 / 503 / network so the UI can show error
  /// vs offline while still allowing retry.
  ///
  /// The pull runs whether or not the push succeeded. It used to return
  /// early on a failed push, so one row the server kept refusing stopped
  /// this device from ever *receiving* anything again either (audit F-11).
  /// A push failure is still reported when the pull itself succeeds.
  Future<SyncResult> synchronize() async {
    final pushed = await push();
    if (pushed.failure == SyncFailureKind.unauthorized) return pushed;
    // Deletes the outbox does not carry (a memory in the server's memory
    // store, a chat transcript) are retried here until confirmed (F-07).
    await RemoteDeletes(_db, _ref, httpClient: _http).flush();
    final pulled = await pull();
    if (!pulled.ok) return pulled;
    if (await adoptDataKey()) {
      final repushed = await push();
      if (!repushed.ok) return repushed;
    }
    return pushed;
  }

  /// ADR-032: fetch the account data key when this device lacks it (or met
  /// a key id it does not know), then move local data off the device key.
  /// Runs right after a pull, so the re-encrypted rows it queues carry the
  /// latest server state. Returns true when it queued uploads. Never throws:
  /// offline, the app simply keeps using the device key and tries again on
  /// the next sync.
  Future<bool> adoptDataKey() async {
    try {
      if (!EncryptionService.hasDataKey || EncryptionService.sawUnknownDataKey) {
        final fetched = await DataKeyClient(_ref, httpClient: _http).fetchAndInstall();
        if (!fetched && !EncryptionService.hasDataKey) return false;
      }
      if (!EncryptionService.hasLegacyKey) return false;
      final report = await DataKeyMigration(_db).run();
      return report.queued > 0 || report.outboxRewrapped > 0;
    } catch (e) {
      debugPrint("[SyncClient] data key migration deferred: ${e.runtimeType}");
      return false;
    }
  }

  /// Mutations per request: bounds the body under the gateway's size limit,
  /// so a large backlog cannot turn into a request that is refused forever.
  static const int pushBatchSize = 100;

  /// Rows per pull page (INT-013): small enough that one page finishes well
  /// inside the request timeout on a slow link, however large the account.
  static const int pullPageSize = 500;

  /// The clock last sent for each record, keyed by table and id (INT-001).
  final Map<String, DateTime> _sentClocks = {};

  /// The `client_updated_at` to send for [e]: its own clock, unless this
  /// client already sent that record a clock at least as new, in which case
  /// one second past that.
  ///
  /// Drift stores the outbox clock in whole seconds, and the server keeps
  /// what it has on a tie (`api/v2/sync.py::is_stale`), so a record changed
  /// twice within a second -- save, then forget; a quick double edit -- sent
  /// its second change with the first one's clock, and the server refused it
  /// as a conflict the client then dropped: the newest edit was lost and a
  /// deleted row stayed alive. Changes to one record go out in the order they
  /// were made (`AppDatabase.pendingOutbox`), so the later send is the newer
  /// change and may claim the later clock. Only a burst of same-second changes
  /// moves a record's clock ahead of the wall clock, by a second per change,
  /// well inside the server's skew allowance.
  DateTime _sendClock(OutboxEntry e) {
    final key = '${e.targetTable}:${e.recordId}';
    final own = e.clientUpdatedAt.toUtc();
    final last = _sentClocks[key];
    final clock = last != null && !own.isAfter(last) ? last.add(const Duration(seconds: 1)) : own;
    _sentClocks[key] = clock;
    return clock;
  }

  Map<String, dynamic> _mutationJson(OutboxEntry e) => {
        "table": e.targetTable,
        "op": e.operation,
        "id": e.recordId,
        "client_updated_at": _sendClock(e).toIso8601String(),
        "payload": e.payloadJson != null ? jsonDecode(e.payloadJson!) : null,
      };

  Future<http.Response?> _postMutations(List<OutboxEntry> entries) {
    return _send((headers) {
      return _post(
        Uri.parse(_syncUrl),
        headers: headers,
        body: jsonEncode({"mutations": entries.map(_mutationJson).toList()}),
      );
    });
  }

  Future<SyncResult> push() async {
    final pending = await _db.pendingOutbox();
    if (pending.isEmpty) return const SyncResult.success();

    try {
      for (var start = 0; start < pending.length; start += pushBatchSize) {
        final end = start + pushBatchSize < pending.length ? start + pushBatchSize : pending.length;
        final outcome = await _pushBatch(pending.sublist(start, end));
        if (!outcome.ok) return outcome;
      }
      return const SyncResult.success();
    } catch (e) {
      debugPrint("[SyncClient] push error: $e");
      return const SyncResult.fail(SyncFailureKind.network);
    }
  }

  /// One request for [batch]. When the server refuses the request as a whole
  /// (a server without per-row isolation answers 500 for one bad row; a 413
  /// or 422 is the same shape), each row is resent on its own so the bad one
  /// is found and the others still land.
  Future<SyncResult> _pushBatch(List<OutboxEntry> batch) async {
    final response = await _postMutations(batch);
    if (response == null) return SyncResult.fail(await _noTokenFailure());
    final refused = _refusal(response);
    if (refused != null) return refused;
    if (response.statusCode == 200) {
      return _acknowledge(batch, response.body);
    }
    debugPrint("[SyncClient] push batch failed: ${response.statusCode}; retrying rows one by one");
    if (batch.length == 1) {
      return _countFailures(batch, "HTTP ${response.statusCode}", isolated: false);
    }

    final rejected = <OutboxEntry>[];
    var anyAccepted = false;
    for (final entry in batch) {
      final single = await _postMutations([entry]);
      if (single == null) return SyncResult.fail(await _noTokenFailure());
      final refusedSingle = _refusal(single);
      if (refusedSingle != null) return refusedSingle;
      if (single.statusCode == 200) {
        anyAccepted = true;
        final acked = await _acknowledge([entry], single.body);
        if (!acked.ok) return acked;
      } else {
        rejected.add(entry);
      }
    }
    // Only count it against the rows when the server took others: when every
    // row fails alike the server is what is broken, and charging attempts
    // would park the whole outbox during an outage.
    return _countFailures(rejected, "HTTP ${response.statusCode}", isolated: anyAccepted);
  }

  /// A failure that is about the session or the server, not a row.
  SyncResult? _refusal(http.Response response) {
    if (response.statusCode == 401) {
      debugPrint("[SyncClient] push unauthorized: ${response.statusCode}");
      return const SyncResult.fail(SyncFailureKind.unauthorized);
    }
    if (response.statusCode == 503) {
      debugPrint("[SyncClient] Postgres sync not configured on the backend yet.");
      return const SyncResult.fail(SyncFailureKind.unavailable);
    }
    return null;
  }

  Future<SyncResult> _countFailures(List<OutboxEntry> rows, String detail, {required bool isolated}) async {
    if (rows.isEmpty) return const SyncResult.success();
    if (!isolated) return const SyncResult.fail(SyncFailureKind.unknown);
    await _recordFailures([
      for (final row in rows) OutboxFailure(outboxId: row.id, detail: detail, retryable: true),
    ], rows);
    return const SyncResult.success();
  }

  Future<void> _recordFailures(List<OutboxFailure> failures, List<OutboxEntry> sent) async {
    final byId = {for (final e in sent) e.id: e};
    for (final failure in failures) {
      final entry = byId[failure.outboxId];
      if (entry == null) continue;
      final attempts = failure.retryable ? entry.attempts + 1 : kMaxOutboxAttempts;
      if (attempts >= kMaxOutboxAttempts) {
        debugPrint("[SyncClient] parking ${entry.targetTable}/${entry.recordId}: ${failure.detail}");
      }
      await (_db.update(_db.outboxEntries)..where((t) => t.id.equals(failure.outboxId))).write(
        OutboxEntriesCompanion(attempts: Value(attempts), lastError: Value(failure.detail)),
      );
    }
  }

  /// Applies one 200 response's per-row results to the outbox.
  Future<SyncResult> _acknowledge(List<OutboxEntry> sent, String body) async {
    final decoded = jsonDecode(body);
    if (decoded is! List) {
      debugPrint("[SyncClient] push response was not a result list; leaving outbox intact");
      return const SyncResult.fail(SyncFailureKind.unknown);
    }
    final ack = classifyOutboxResults(
      pendingIds: sent.map((e) => e.id).toList(),
      results: decoded,
    );
    // Applied and conflicted rows are done from this client's point of view;
    // a conflict's server row (and a merged row's) is applied right here.
    // Error rows stay in the outbox -- retried, or parked when the server
    // says retrying cannot help.
    await _db.transaction(() async {
      if (ack.drop.isNotEmpty) {
        await (_db.delete(_db.outboxEntries)..where((t) => t.id.isIn(ack.drop))).go();
      }
      await _recordFailures(ack.failures, sent);
      if (ack.serverRows.isNotEmpty) await _applyRows(ack.serverRows);
    });
    return const SyncResult.success();
  }

  /// Pulls everything changed since the stored cursor, one page at a time
  /// (INT-013).
  ///
  /// A first sync on a new device used to be one response holding every row
  /// of the account, against a 20 s timeout; a large account could time out
  /// on every attempt and never finish. Each page is applied in its own
  /// transaction, and the cursor is stored only from the last page, so a pull
  /// cut off midway starts again from the old cursor -- the rows already
  /// applied are simply applied again. A server that predates paging ignores
  /// `limit` and answers in one response without `has_more`.
  Future<SyncResult> pull() async {
    final cursor = await _db.getSyncCursor();
    String? page;

    try {
      while (true) {
        final uri = Uri.parse(_syncUrl).replace(queryParameters: {
          "limit": "$pullPageSize",
          // Sent with `page` too: a server that cannot honour the token
          // starts over from here rather than from a full sync.
          "since": ?cursor,
          "page": ?page,
        });
        final response = await _send((headers) => _get(uri, headers: headers));
        if (response == null) return SyncResult.fail(await _noTokenFailure());
        if (response.statusCode == 401) {
          debugPrint("[SyncClient] pull unauthorized: ${response.statusCode}");
          return const SyncResult.fail(SyncFailureKind.unauthorized);
        }
        if (response.statusCode == 503) {
          debugPrint("[SyncClient] Postgres sync not configured on the backend yet.");
          return const SyncResult.fail(SyncFailureKind.unavailable);
        }
        if (response.statusCode != 200) {
          debugPrint("[SyncClient] pull failed: ${response.statusCode} ${response.body}");
          return const SyncResult.fail(SyncFailureKind.unknown);
        }

        final body = jsonDecode(response.body) as Map<String, dynamic>;
        final data = body["data"] as Map<String, dynamic>;

        await _db.transaction(() => _applyRows(data));

        final next = body["next_page"];
        if (body["has_more"] == true && next is String) {
          if (next == page) {
            debugPrint("[SyncClient] pull did not advance; stopping before the last page");
            return const SyncResult.fail(SyncFailureKind.unknown);
          }
          page = next;
          continue;
        }
        await _db.setSyncCursor(body["cursor"] as String);
        return const SyncResult.success();
      }
    } catch (e) {
      debugPrint("[SyncClient] pull error: $e");
      return const SyncResult.fail(SyncFailureKind.network);
    }
  }

  DateTime? _parseDate(dynamic v) => v == null ? null : DateTime.parse(v as String).toLocal();

  /// Applies server rows, keyed by table, onto Drift. Used for a pull's
  /// `data` and for the rows a push's conflict and merge results carry.
  /// Callers wrap it in a transaction.
  Future<void> _applyRows(Map<String, dynamic> data) async {
    List<dynamic> rows(String table) => data[table] as List<dynamic>? ?? const [];
    await _applyTasks(rows("tasks"));
    await _applyCalendarEvents(rows("calendar_events"));
    await _applyFinanceRecords(rows("finance_records"));
    await _applyHealthRecords(rows("health_records"));
    await _applyMessages(rows("messages"));
    await _applyProjects(rows("projects"));
    await _applyGoals(rows("goals"));
    await _applyMemoryTombstones(rows("memories"));
  }

  /// A memory forgotten on another device arrives as a tombstone under the
  /// server's memory id, which every device's local copy now shares (F-07).
  /// Live memory rows are not applied from here: the memory list is the
  /// server's memory store (`MemoryRepository.syncMemories`), and these rows
  /// are only its encrypted copy.
  Future<void> _applyMemoryTombstones(List<dynamic> rows) async {
    final forgotten = [
      for (final raw in rows)
        if ((raw as Map<String, dynamic>)["deleted_at"] != null) raw["id"] as String,
    ];
    if (forgotten.isEmpty) return;
    await (_db.delete(_db.userMemories)..where((m) => m.id.isIn(forgotten))).go();
  }

  Future<void> _applyTasks(List<dynamic> rows) async {
    for (final raw in rows) {
      final row = raw as Map<String, dynamic>;
      final id = row["id"] as String;
      if (row["deleted_at"] != null) {
        await (_db.delete(_db.tasks)..where((t) => t.id.equals(id))).go();
        continue;
      }
      await _db.into(_db.tasks).insertOnConflictUpdate(TasksCompanion(
            id: Value(id),
            title: Value(EncryptionService.decryptText(row["title_enc"] as String)),
            description: Value(row["description_enc"] != null ? EncryptionService.decryptText(row["description_enc"] as String) : null),
            priority: Value(row["priority"] as String? ?? "medium"),
            category: Value(row["category"] as String? ?? "personal"),
            dueDate: Value(_parseDate(row["due_date"])),
            isCompleted: Value(row["completed_at"] != null),
            googleTaskId: Value(row["google_task_id"] as String?),
            createdAt: Value(_parseDate(row["created_at"]) ?? DateTime.now()),
            isSynced: const Value(true),
            updatedAt: Value(_parseDate(row["updated_at"])),
          ));
    }
  }

  Future<void> _applyCalendarEvents(List<dynamic> rows) async {
    for (final raw in rows) {
      final row = raw as Map<String, dynamic>;
      final id = row["id"] as String;
      if (row["deleted_at"] != null) {
        await (_db.delete(_db.calendarEvents)..where((t) => t.id.equals(id))).go();
        continue;
      }
      await _db.into(_db.calendarEvents).insertOnConflictUpdate(CalendarEventsCompanion(
            id: Value(id),
            title: Value(EncryptionService.decryptText(row["title_enc"] as String)),
            description: Value(row["description_enc"] != null ? EncryptionService.decryptText(row["description_enc"] as String) : null),
            startTime: Value(_parseDate(row["start_time"])!),
            endTime: Value(_parseDate(row["end_time"])!),
            isAllDay: Value(row["all_day"] as bool? ?? false),
            googleEventId: Value(row["google_event_id"] as String?),
            createdAt: Value(_parseDate(row["created_at"]) ?? DateTime.now()),
            isSynced: const Value(true),
            updatedAt: Value(_parseDate(row["updated_at"])),
          ));
    }
  }

  Future<void> _applyFinanceRecords(List<dynamic> rows) async {
    for (final raw in rows) {
      final row = raw as Map<String, dynamic>;
      final id = row["id"] as String;
      if (row["deleted_at"] != null) {
        await (_db.delete(_db.financeRecords)..where((t) => t.id.equals(id))).go();
        continue;
      }
      await _db.into(_db.financeRecords).insertOnConflictUpdate(FinanceRecordsCompanion(
            id: Value(id),
            type: Value(row["type"] as String),
            amount: Value((row["amount"] as num).toDouble()),
            category: Value(row["category"] as String? ?? "other"),
            description: Value(row["description_enc"] != null ? EncryptionService.decryptText(row["description_enc"] as String) : null),
            date: Value(_parseDate(row["occurred_at"])!),
            createdAt: Value(_parseDate(row["created_at"]) ?? DateTime.now()),
            isSynced: const Value(true),
            updatedAt: Value(_parseDate(row["updated_at"])),
          ));
    }
  }

  Future<void> _applyHealthRecords(List<dynamic> rows) async {
    for (final raw in rows) {
      final row = raw as Map<String, dynamic>;
      final id = row["id"] as String;
      if (row["deleted_at"] != null) {
        await (_db.delete(_db.healthRecords)..where((t) => t.id.equals(id))).go();
        continue;
      }
      // A calendar day, not an instant: never shifted to local time (F4).
      final recordDate = healthDayOfInstant(DateTime.parse(row["record_date"] as String));
      // One record per day: the server merges a second device's "today" onto
      // the row it already had (audit F-11), so a local row for the same day
      // under another id is that device's own copy. Replace it, or the day
      // has two rows and every single-row lookup for it throws.
      await (_db.delete(_db.healthRecords)
            ..where((t) => t.recordDate.equals(recordDate) & t.id.equals(id).not()))
          .go();
      await _db.into(_db.healthRecords).insertOnConflictUpdate(HealthRecordsCompanion(
            id: Value(id),
            recordDate: Value(recordDate),
            weight: Value((row["weight"] as num?)?.toDouble()),
            waterIntakeMl: Value(row["water_ml"] as int? ?? 0),
            sleepMinutes: Value(row["sleep_minutes"] as int? ?? 0),
            caloriesConsumed: Value(row["calories"] as int? ?? 0),
            exerciseMinutes: Value(row["exercise_minutes"] as int? ?? 0),
            createdAt: Value(_parseDate(row["created_at"]) ?? DateTime.now()),
            isSynced: const Value(true),
            updatedAt: Value(_parseDate(row["updated_at"])),
          ));
    }
  }

  Future<void> _applyMessages(List<dynamic> rows) async {
    for (final raw in rows) {
      final row = raw as Map<String, dynamic>;
      final id = row["id"] as String;
      if (row["deleted_at"] != null) {
        await (_db.delete(_db.messages)..where((t) => t.id.equals(id))).go();
        continue;
      }
      final contentEnc = row["content_enc"] as String?;
      if (contentEnc == null) {
        debugPrint("[SyncClient] skipping message $id: missing content_enc");
        continue;
      }
      await _db.into(_db.messages).insertOnConflictUpdate(MessagesCompanion(
            id: Value(id),
            conversationId: Value(row["conversation_id"] as String),
            role: Value(row["role"] as String),
            content: Value(EncryptionService.decryptText(contentEnc)),
            modelUsed: Value(row["model_used"] as String?),
            tokensUsed: Value(row["tokens_used"] as int?),
            citationsJson: Value(
              row["citations_enc"] != null
                  ? EncryptionService.decryptText(row["citations_enc"] as String)
                  : null,
            ),
            toolCallsJson: Value(
              row["tool_calls_enc"] != null
                  ? EncryptionService.decryptText(row["tool_calls_enc"] as String)
                  : null,
            ),
            agentId: Value(row["persona"] as String?),
            createdAt: Value(_parseDate(row["created_at"]) ?? DateTime.now()),
            updatedAt: Value(_parseDate(row["updated_at"])),
          ));
    }
  }

  Future<void> _applyProjects(List<dynamic> rows) async {
    for (final raw in rows) {
      final row = raw as Map<String, dynamic>;
      final id = row["id"] as String;
      if (row["deleted_at"] != null) {
        await (_db.delete(_db.projects)..where((t) => t.id.equals(id))).go();
        continue;
      }
      await _db.into(_db.projects).insertOnConflictUpdate(ProjectsCompanion(
            id: Value(id),
            title: Value(EncryptionService.decryptText(row["title_enc"] as String)),
            description: Value(row["description_enc"] != null ? EncryptionService.decryptText(row["description_enc"] as String) : null),
            status: Value(row["status"] as String? ?? "active"),
            targetDate: Value(_parseDate(row["target_date"])),
            createdAt: Value(_parseDate(row["created_at"]) ?? DateTime.now()),
            isSynced: const Value(true),
            updatedAt: Value(_parseDate(row["updated_at"])),
          ));
    }
  }

  Future<void> _applyGoals(List<dynamic> rows) async {
    for (final raw in rows) {
      final row = raw as Map<String, dynamic>;
      final id = row["id"] as String;
      if (row["deleted_at"] != null) {
        await (_db.delete(_db.goals)..where((t) => t.id.equals(id))).go();
        continue;
      }
      await _db.into(_db.goals).insertOnConflictUpdate(GoalsCompanion(
            id: Value(id),
            title: Value(EncryptionService.decryptText(row["title_enc"] as String)),
            description: Value(row["description_enc"] != null ? EncryptionService.decryptText(row["description_enc"] as String) : null),
            projectId: Value(row["project_id"] as String?),
            category: Value(row["category"] as String?),
            status: Value(row["status"] as String? ?? "active"),
            priority: Value(row["priority"] as String? ?? "medium"),
            targetDate: Value(_parseDate(row["target_date"])),
            progressPct: Value(row["progress_pct"] as int? ?? 0),
            createdAt: Value(_parseDate(row["created_at"]) ?? DateTime.now()),
            isSynced: const Value(true),
            updatedAt: Value(_parseDate(row["updated_at"])),
          ));
    }
  }
}

final syncClientProvider = Provider<SyncClient>((ref) {
  final db = ref.watch(databaseProvider);
  return SyncClient(db, ref);
});

/// Outbox rows the server keeps refusing (parked; see `OutboxEntries.attempts`).
final parkedOutboxCountProvider = StreamProvider<int>((ref) {
  return ref.watch(databaseProvider).watchParkedOutboxCount();
});
