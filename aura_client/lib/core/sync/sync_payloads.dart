import '../database/database.dart';
import '../database/health_day.dart';
import '../security/encryption_service.dart';

/// Builds outbox payloads matching the backend's Postgres column names
/// (aura_backend/infrastructure/db/models.py) from local Drift row objects.
/// Centralized here so every repository's mutation methods stay one line
/// (`_db.enqueueOutbox(..., payload: taskSyncPayload(task))`) instead of
/// each repo hand-rolling its own field mapping and encryption calls.
class SyncPayloads {
  SyncPayloads._();

  static Map<String, dynamic> task(Task t) => {
        "title_enc": EncryptionService.encryptText(t.title),
        "description_enc": t.description != null ? EncryptionService.encryptText(t.description!) : null,
        "priority": t.priority,
        "category": t.category,
        "due_date": t.dueDate?.toUtc().toIso8601String(),
        "completed_at": t.isCompleted ? (t.updatedAt ?? DateTime.now()).toUtc().toIso8601String() : null,
        "google_task_id": t.googleTaskId,
      };

  static Map<String, dynamic> calendarEvent(CalendarEvent e) => {
        "title_enc": EncryptionService.encryptText(e.title),
        "description_enc": e.description != null ? EncryptionService.encryptText(e.description!) : null,
        "start_time": e.startTime.toUtc().toIso8601String(),
        "end_time": e.endTime.toUtc().toIso8601String(),
        "all_day": e.isAllDay,
        "google_event_id": e.googleEventId,
      };

  static Map<String, dynamic> financeRecord(FinanceRecord r) => {
        "type": r.type,
        "amount": r.amount,
        "category": r.category,
        "description_enc": r.description != null ? EncryptionService.encryptText(r.description!) : null,
        "occurred_at": r.date.toUtc().toIso8601String(),
      };

  static Map<String, dynamic> healthRecord(HealthRecord r) => {
        // The calendar day as its UTC midnight, the server's key for it (F4).
        "record_date": healthDayOfInstant(r.recordDate).toIso8601String(),
        "weight": r.weight,
        "water_ml": r.waterIntakeMl,
        "sleep_minutes": r.sleepMinutes,
        "calories": r.caloriesConsumed,
        "exercise_minutes": r.exerciseMinutes,
      };

/// Postgres `messages` columns (AURA-014). Drift `agentId` is `persona`;
/// `citationsJson` / `toolCallsJson` travel as `citations_enc` /
/// `tool_calls_enc`. Ciphertext is produced here from already-decrypted
/// Drift fields, matching [task].
  static Map<String, dynamic> message(Message m) => {
        "conversation_id": m.conversationId,
        "role": m.role,
        "content_enc": EncryptionService.encryptText(m.content),
        "model_used": m.modelUsed,
        "tokens_used": m.tokensUsed,
        "citations_enc": m.citationsJson != null ? EncryptionService.encryptText(m.citationsJson!) : null,
        "tool_calls_enc": m.toolCallsJson != null ? EncryptionService.encryptText(m.toolCallsJson!) : null,
        "persona": m.agentId,
      };

  static Map<String, dynamic> project(Project p) => {
        "title_enc": EncryptionService.encryptText(p.title),
        "description_enc": p.description != null ? EncryptionService.encryptText(p.description!) : null,
        "status": p.status,
        "target_date": p.targetDate?.toUtc().toIso8601String(),
      };

  static Map<String, dynamic> goal(Goal g) => {
        "title_enc": EncryptionService.encryptText(g.title),
        "description_enc": g.description != null ? EncryptionService.encryptText(g.description!) : null,
        "project_id": g.projectId,
        "category": g.category,
        "status": g.status,
        "priority": g.priority,
        "target_date": g.targetDate?.toUtc().toIso8601String(),
        "progress_pct": g.progressPct,
      };

  /// Postgres `memories.fact_enc` (NOT NULL). This was sent as `content_enc`,
  /// which the server ignores as an unknown key, so no memory row could ever
  /// be inserted (audit F-13). Pinned by `test/sync_payload_contract_test.dart`.
  static Map<String, dynamic> userMemory(String fact) => {
        "fact_enc": EncryptionService.encryptText(fact),
      };
}
