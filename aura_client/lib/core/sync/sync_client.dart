import 'dart:convert';
import 'package:drift/drift.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../auth/token_manager.dart';
import '../config/config.dart';
import '../database/database.dart';
import '../database/database_provider.dart';
import '../security/device_validation_service.dart';
import '../security/encryption_service.dart';
import 'outbox_ack.dart';
import 'sync_result.dart';

/// V2 cursor-based sync client (Doc/V2_BLUEPRINT.md §5/§7) — talks to
/// `/api/v2/sync` on the FastAPI backend, which owns Postgres as the
/// relational source of truth. Replaces V1's `SyncEngine`, which wrote
/// directly to Firestore from the client and hand-rolled five nearly
/// identical last-write-wins comparators (577 lines). Here the server does
/// the diffing (`WHERE user_id = ? AND updated_at > ?`); the client only
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
  static const String _syncUrl = "${AppConfig.backendUrl}/api/v2/sync";

  SyncClient(this._db, this._ref, {http.Client? httpClient}) : _http = httpClient;

  // v2 endpoints authenticate via TokenManager's backend-issued JWT
  // (Doc/V2_BLUEPRINT.md §9), not the raw Google ID token v1 endpoints use.
  Future<Map<String, String>> _headers() async {
    final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
    final headers = <String, String>{"Content-Type": "application/json"};
    if (token != null) headers["Authorization"] = "Bearer $token";
    headers.addAll(await DeviceValidationService.getAttestationHeaders());
    return headers;
  }

  /// One 401 retry after dropping the cached access token. Sync used to
  /// replay a rejected JWT until its local 15-minute expiry.
  Future<http.Response> _send(Future<http.Response> Function() send) async {
    var response = await send();
    if (response.statusCode == 401) {
      debugPrint("[SyncClient] 401 — invalidating access token and retrying once");
      _ref.read(tokenManagerProvider).invalidateAccessToken();
      response = await send();
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
  Future<SyncResult> synchronize() async {
    final pushed = await push();
    if (!pushed.ok) return pushed;
    return pull();
  }

  Future<SyncResult> push() async {
    final pending = await _db.select(_db.outboxEntries).get();
    if (pending.isEmpty) return const SyncResult.success();

    final mutations = pending
        .map((e) => {
              "table": e.targetTable,
              "op": e.operation,
              "id": e.recordId,
              "client_updated_at": e.clientUpdatedAt.toUtc().toIso8601String(),
              "payload": e.payloadJson != null ? jsonDecode(e.payloadJson!) : null,
            })
        .toList();

    try {
      final response = await _send(() async {
        return _post(
          Uri.parse(_syncUrl),
          headers: await _headers(),
          body: jsonEncode({"mutations": mutations}),
        );
      });

      if (response.statusCode == 401) {
        debugPrint("[SyncClient] push unauthorized: ${response.statusCode}");
        return const SyncResult.fail(SyncFailureKind.unauthorized);
      }
      if (response.statusCode == 503) {
        debugPrint("[SyncClient] Postgres sync not configured on the backend yet.");
        return const SyncResult.fail(SyncFailureKind.unavailable);
      }
      if (response.statusCode != 200) {
        debugPrint("[SyncClient] push failed: ${response.statusCode} ${response.body}");
        return const SyncResult.fail(SyncFailureKind.unknown);
      }

      // Applied and conflicted rows are done from this client's point of
      // view (a conflict is resolved by the subsequent pull). Error rows
      // stay in the outbox for retry — HTTP 200 used to wipe them too.
      final decoded = jsonDecode(response.body);
      if (decoded is! List) {
        debugPrint("[SyncClient] push response was not a result list; leaving outbox intact");
        return const SyncResult.fail(SyncFailureKind.unknown);
      }
      final idsToDrop = outboxIdsToDrop(
        pendingIds: pending.map((e) => e.id).toList(),
        results: decoded,
      );
      if (idsToDrop.isNotEmpty) {
        await (_db.delete(_db.outboxEntries)..where((t) => t.id.isIn(idsToDrop))).go();
      }
      return const SyncResult.success();
    } catch (e) {
      debugPrint("[SyncClient] push error: $e");
      return const SyncResult.fail(SyncFailureKind.network);
    }
  }

  Future<SyncResult> pull() async {
    final cursor = await _db.getSyncCursor();
    final uri = Uri.parse(_syncUrl).replace(queryParameters: cursor != null ? {"since": cursor} : null);

    try {
      final response = await _send(() async {
        return _get(uri, headers: await _headers());
      });
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

      await _db.transaction(() async {
        await _applyTasks(data["tasks"] as List<dynamic>? ?? const []);
        await _applyCalendarEvents(data["calendar_events"] as List<dynamic>? ?? const []);
        await _applyFinanceRecords(data["finance_records"] as List<dynamic>? ?? const []);
        await _applyHealthRecords(data["health_records"] as List<dynamic>? ?? const []);
        await _applyMessages(data["messages"] as List<dynamic>? ?? const []);
      });

      await _db.setSyncCursor(body["cursor"] as String);
      return const SyncResult.success();
    } catch (e) {
      debugPrint("[SyncClient] pull error: $e");
      return const SyncResult.fail(SyncFailureKind.network);
    }
  }

  DateTime? _parseDate(dynamic v) => v == null ? null : DateTime.parse(v as String).toLocal();

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
      await _db.into(_db.healthRecords).insertOnConflictUpdate(HealthRecordsCompanion(
            id: Value(id),
            recordDate: Value(_parseDate(row["record_date"])!),
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
}

final syncClientProvider = Provider<SyncClient>((ref) {
  final db = ref.watch(databaseProvider);
  return SyncClient(db, ref);
});
