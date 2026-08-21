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

/// V2 cursor-based sync client (Doc/V2_BLUEPRINT.md §5/§7) — talks to
/// `/api/v2/sync` on the FastAPI backend, which owns Postgres as the
/// relational source of truth. Replaces V1's `SyncEngine`, which wrote
/// directly to Firestore from the client and hand-rolled five nearly
/// identical last-write-wins comparators (577 lines). Here the server does
/// the diffing (`WHERE user_id = ? AND updated_at > ?`); the client only
/// has to (1) push its outbox and (2) apply whatever rows come back.
///
/// Scope note: Tasks/CalendarEvents/FinanceRecords/HealthRecords are wired
/// to this protocol. UserMemories stay on the existing Qdrant-backed
/// `/api/memory/*` endpoints (MemoryRepository) for now — that data is
/// shaped around embeddings, not a plain CRUD row, and folding it into the
/// same cursor protocol is Phase 1.5/2 work, not part of this rebuild.
class SyncClient {
  final AppDatabase _db;
  final Ref _ref;
  static const String _syncUrl = "${AppConfig.backendUrl}/api/v2/sync";

  SyncClient(this._db, this._ref);

  // v2 endpoints authenticate via TokenManager's backend-issued JWT
  // (Doc/V2_BLUEPRINT.md §9), not the raw Google ID token v1 endpoints use.
  Future<Map<String, String>> _headers() async {
    final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
    final headers = <String, String>{"Content-Type": "application/json"};
    if (token != null) headers["Authorization"] = "Bearer $token";
    headers.addAll(DeviceValidationService.getAttestationHeaders());
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

  /// Pushes the outbox, then pulls everything changed since the last
  /// cursor. Returns true if both phases completed (a 503 from an
  /// unconfigured backend, or a network failure, returns false so the
  /// caller can show an "offline" state rather than a crash).
  Future<bool> synchronize() async {
    final pushed = await push();
    if (!pushed) return false;
    return pull();
  }

  Future<bool> push() async {
    final pending = await _db.select(_db.outboxEntries).get();
    if (pending.isEmpty) return true;

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
        return http
            .post(Uri.parse(_syncUrl), headers: await _headers(), body: jsonEncode({"mutations": mutations}))
            .timeout(const Duration(seconds: 20));
      });

      if (response.statusCode == 503) {
        debugPrint("[SyncClient] Postgres sync not configured on the backend yet.");
        return false;
      }
      if (response.statusCode != 200) {
        debugPrint("[SyncClient] push failed: ${response.statusCode} ${response.body}");
        return false;
      }

      // Every mutation the server responded to (applied OR conflict) is
      // done from this client's point of view — a conflict means the
      // server holds a newer value, which the subsequent pull() brings
      // down; retrying the same push would just conflict again.
      final ids = pending.map((e) => e.id).toList();
      await (_db.delete(_db.outboxEntries)..where((t) => t.id.isIn(ids))).go();
      return true;
    } catch (e) {
      debugPrint("[SyncClient] push error: $e");
      return false;
    }
  }

  Future<bool> pull() async {
    final cursor = await _db.getSyncCursor();
    final uri = Uri.parse(_syncUrl).replace(queryParameters: cursor != null ? {"since": cursor} : null);

    try {
      final response = await _send(() async {
        return http.get(uri, headers: await _headers()).timeout(const Duration(seconds: 20));
      });
      if (response.statusCode == 503) {
        debugPrint("[SyncClient] Postgres sync not configured on the backend yet.");
        return false;
      }
      if (response.statusCode != 200) {
        debugPrint("[SyncClient] pull failed: ${response.statusCode} ${response.body}");
        return false;
      }

      final body = jsonDecode(response.body) as Map<String, dynamic>;
      final data = body["data"] as Map<String, dynamic>;

      await _db.transaction(() async {
        await _applyTasks(data["tasks"] as List<dynamic>? ?? const []);
        await _applyCalendarEvents(data["calendar_events"] as List<dynamic>? ?? const []);
        await _applyFinanceRecords(data["finance_records"] as List<dynamic>? ?? const []);
        await _applyHealthRecords(data["health_records"] as List<dynamic>? ?? const []);
      });

      await _db.setSyncCursor(body["cursor"] as String);
      return true;
    } catch (e) {
      debugPrint("[SyncClient] pull error: $e");
      return false;
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
}

final syncClientProvider = Provider<SyncClient>((ref) {
  final db = ref.watch(databaseProvider);
  return SyncClient(db, ref);
});
